"""Generate local protocol load and count live browser deliveries."""

import asyncio
import json
import math
import os
import platform
from contextlib import nullcontext
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import urlsplit

import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from cads.protocol import Lane, Outcome, Pose, RunConfig, Step
from cads.vehicle import RunError, Simulator, VehicleLink


def percentiles(samples: list[float]) -> dict | None:
    if not samples:
        return None
    p50, p95, p99 = np.percentile(samples, [50, 95, 99])
    return {"p50": float(p50), "p95": float(p95), "p99": float(p99), "samples": len(samples)}


async def viewer(url: str, samples: list[float], ready: asyncio.Event) -> None:
    try:
        async with connect(url) as socket:
            while True:
                message = json.loads(await socket.recv())
                if message["type"] == "run":
                    ready.set()
                elif message["type"] == "step":
                    committed = datetime.fromisoformat(message["record"]["committed_at"])
                    samples.append(max(0, (datetime.now(UTC) - committed).total_seconds() * 1000))
                elif message["type"] == "status":
                    return
                elif message["type"] == "error":
                    raise RunError(message["message"])
    finally:
        ready.set()


async def measure(args, config: RunConfig) -> None:
    if args.viewers < 0 or not math.isfinite(args.rate) or args.rate < 0 or config.seed + args.runs > 2**32:
        raise ValueError("Viewer count, rate or seed range is invalid")
    base = urlsplit(args.url)
    viewer_base = f"{base.scheme}://{base.netloc}/ws/runs"
    barrier = asyncio.Barrier(args.runs)
    latencies, deliveries, errors = [], [], []
    counters = {
        "admitted": 0,
        "completed": 0,
        "refused": 0,
        "failed": 0,
        "committed_steps": 0,
        "viewer_failures": 0,
    }

    async def client(number: int) -> None:
        viewers = []
        crossed = False
        try:
            run_config = config.model_copy(update={"seed": config.seed + number})
            with Simulator(run_config.seed) if args.simulate else nullcontext() as simulator:
                lanes = (
                    simulator.lanes()
                    if simulator
                    else [Lane(y=4.0 * lane, width=4.0, start=0.0, end=10000.0) for lane in range(4)]
                )
                async with connect(args.url) as socket:
                    link = VehicleLink(socket)
                    run_id = await link.open(run_config, lanes)
                    counters["admitted"] += 1
                    signals = [asyncio.Event() for _ in range(args.viewers)]
                    viewers = [
                        asyncio.create_task(viewer(f"{viewer_base}/{run_id}", deliveries, ready))
                        for ready in signals
                    ]
                    await asyncio.wait_for(asyncio.gather(*(ready.wait() for ready in signals)), timeout=30)
                    await barrier.wait()
                    crossed = True
                    for seq in range(config.max_steps):
                        tick = perf_counter()
                        poses = [Pose(id=0, ego=True, x=float(seq * 25), y=4.0, speed=25.0, heading=0.0)]
                        step = (
                            simulator.step(seq)
                            if simulator
                            else Step(
                                seq=seq, vehicles=poses, observation=[1.0, 0.0, 0.0, 0.3, 0.0] + [0.0] * 45
                            )
                        )
                        action, rtt = await link.act(step)
                        latencies.append(rtt)
                        outcome = (
                            await asyncio.to_thread(simulator.apply, seq, action)
                            if simulator
                            else Outcome(
                                seq=seq,
                                reward=0.0,
                                terminated=False,
                                truncated=False,
                                crashed=False,
                                vehicles=poses,
                            )
                        )
                        outcome.client_rtt_ms = rtt
                        done = await link.finish_step(outcome)
                        counters["committed_steps"] += 1
                        if done:
                            counters["completed"] += 1
                            return
                        if args.rate:
                            await asyncio.sleep(max(0, 1 / args.rate - (perf_counter() - tick)))
        except (RunError, TimeoutError, OSError, WebSocketException) as exc:
            if str(exc).startswith("OVERLOADED:"):
                counters["refused"] += 1
            else:
                counters["failed"] += 1
                errors.append(str(exc))
        finally:
            if not crossed:
                await barrier.wait()
            if viewers:
                _, pending = await asyncio.wait(viewers, timeout=10)
                for task in pending:
                    task.cancel()
                outcomes = await asyncio.gather(*viewers, return_exceptions=True)
                counters["viewer_failures"] += sum(isinstance(item, BaseException) for item in outcomes)

    start = perf_counter()
    await asyncio.gather(*(client(index) for index in range(args.runs)))
    elapsed = perf_counter() - start
    result = {
        "timestamp": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "mode": "simulator" if args.simulate else "synthetic",
        "config": config.model_dump(),
        "runs_requested": args.runs,
        "viewers_per_run": args.viewers,
        "target_rate_per_run": args.rate,
        **counters,
        "elapsed_seconds": elapsed,
        "steps_per_second_including_startup": counters["committed_steps"] / elapsed,
        "vehicle_rtt_ms": percentiles(latencies),
        "viewer_delay_ms": percentiles(deliveries),
        "errors": sorted(set(errors)),
    }
    serialized = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    if counters["failed"] or counters["viewer_failures"]:
        raise RunError("Load test recorded failed clients or viewers")
