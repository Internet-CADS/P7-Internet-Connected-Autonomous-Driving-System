"""`icads loadgen <profile>`: replay vehicles and simulated viewers (docs/ARCHITECTURE.md, sections 10 and 15).

A replay vehicle sends a recorded run (an exported step log) through the gateway step by step and
ignores the actions it gets back; no highway-env, so one process drives many runs. Its runs are
labelled `synthetic`. Viewers follow runs over server-sent events, like browsers.

Measured with the load generator's own clock (one process, `time.perf_counter`):

- vehicle round-trip time: `Obs` sent to `Action` received
- viewer delay: `Result` sent to the matching step event received
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import grpc
import numpy as np

from icads.loadgen.profile import Phase, Profile
from icads.protocol import gateway_pb2 as pb
from icads.protocol import gateway_pb2_grpc as rpc
from icads.protocol.reference import PROTOCOL_VERSION, SCENARIO
from icads.protocol.runtime import image_identity, service_cpu_limit
from icads.protocol.serving import KEEPALIVE_OPTIONS


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0, "p50": float("nan"), "p95": float("nan"), "p99": float("nan"), "max": float("nan")}
    a = np.asarray(values, dtype=np.float64)
    return {"n": int(a.size), "p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)),
            "p99": float(np.percentile(a, 99)), "max": float(a.max())}


# ---------------------------------------------------------------- the recording
@dataclass
class Recording:
    run: dict[str, Any]
    steps: list[dict[str, Any]]
    end: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "Recording":
        lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        run = next(x for x in lines if x["type"] == "run")
        end = next((x for x in lines if x["type"] == "end"), {})
        steps = [x for x in lines if x["type"] == "step"]
        if not steps:
            raise ValueError(f"{path} has no steps")
        return cls(run, steps, end)

    def hello(self, phase: Phase) -> pb.Hello:
        r = self.run
        spec = r["obsSpec"]
        return pb.Hello(
            protocol_version=PROTOCOL_VERSION, scenario=r.get("scenario") or SCENARIO,
            env_config_json=json.dumps(r["envConfig"]), seed=int(r["seed"]), max_steps=len(self.steps),
            model_id=phase.model or r["modelId"], attack=phase.attack, epsilon=phase.epsilon,
            pace_hz=phase.pace_hz,
            obs_spec=pb.ObsSpec(features=spec["features"], vehicles=spec["vehicles"], absolute=spec["absolute"],
                                ranges={k: pb.Range(low=v[0], high=v[1]) for k, v in spec["ranges"].items()}),
            action_names=r["actionNames"],
            road=pb.Road(lanes=[pb.Lane(polyline=[pb.Point(x=p[0], y=p[1]) for p in lane["polyline"]],
                                        width=lane["width"]) for lane in r["road"]["lanes"]]),
            labels={"synthetic": "true", "loadgen": phase.label()})


def _poses(rows) -> list[pb.Pose]:
    return [pb.Pose(id=int(p[0]), x=p[1], y=p[2], heading=p[3], speed=p[4]) for p in rows or []]


def record(profile: Profile, gateway: str, http: str) -> None:
    """Make the recording with one real vehicle run (labelled synthetic), exported through REST.
    The vehicle runs as its own `icads vehicle` process (components do not import each other)."""
    cmd = [sys.executable, "-m", "icads.cli", "vehicle", "--gateway", gateway, "--quiet",
           "--model", profile.record_model, "--seed", str(profile.record_seed),
           "--label", "synthetic=true", "--label", "loadgen=recording"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    lines = proc.stdout.strip().splitlines()
    out = json.loads(lines[-1]) if lines else {}
    if out.get("status") != "finished":
        raise RuntimeError(f"could not record a run: {out or proc.stderr.strip()}")
    with urllib.request.urlopen(f"{http}/v1/runs/{out['runId']}/log", timeout=60) as r:
        data = r.read()
    profile.recording_path.parent.mkdir(parents=True, exist_ok=True)
    profile.recording_path.write_bytes(data)
    print(f"recorded {out['steps']} steps of {profile.record_model} to {profile.recording_path}", file=sys.stderr)


# ---------------------------------------------------------------- one phase
@dataclass
class Metrics:
    rtt_ms: list[float] = field(default_factory=list)
    viewer_ms: list[float] = field(default_factory=list)
    result_sent: dict[tuple[str, int], float] = field(default_factory=dict)
    steps: int = 0
    started: int = 0
    finished: int = 0
    failed: int = 0
    refused: int = 0
    failures: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    viewer_errors: int = 0
    load: list[dict[str, Any]] = field(default_factory=list)


async def viewer(session: aiohttp.ClientSession, http: str, run_id: str, m: Metrics) -> None:
    try:
        async with session.get(f"{http}/v1/runs/{run_id}/events", timeout=aiohttp.ClientTimeout(total=None)) as resp:
            name, ev_id = None, None
            async for raw in resp.content:
                line = raw.decode().rstrip("\n")
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("id:"):
                    ev_id = int(line[3:].strip())
                elif line == "":
                    if name == "step" and ev_id is not None:
                        sent = m.result_sent.get((run_id, ev_id))
                        if sent is not None:
                            m.viewer_ms.append((time.perf_counter() - sent) * 1000.0)
                    if name == "end":
                        return
                    name, ev_id = None, None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        m.viewer_errors += 1


async def replay_once(stub: rpc.GatewayStub, rec: Recording, phase: Phase, m: Metrics,
                      session: aiohttp.ClientSession, http: str) -> bool:
    """One replayed run. Returns False if it was refused."""
    call = stub.Drive()
    viewers: list[asyncio.Task] = []
    try:
        await call.write(pb.VehicleMsg(hello=rec.hello(phase)))
        first = await call.read()
        if first.WhichOneof("msg") == "summary":
            m.refused += 1
            return False
        run_id = first.welcome.run_id
        m.started += 1
        m.run_ids.append(run_id)
        viewers = [asyncio.create_task(viewer(session, http, run_id, m)) for _ in range(phase.viewers_per_run)]
        period = 1.0 / phase.pace_hz if phase.pace_hz > 0 else 0.0
        last_t = len(rec.steps) - 1
        for s in rec.steps:
            step_start = time.perf_counter()
            await call.write(pb.VehicleMsg(obs=pb.Obs(t=s["t"], obs=s["obs"], poses=_poses(s["poses"]))))
            reply = await call.read()
            if reply.WhichOneof("msg") != "action":
                m.failed += 1
                m.failures.append(f"{reply.summary.status} {reply.summary.reason}")
                return True
            rtt = (time.perf_counter() - step_start) * 1000.0
            m.rtt_ms.append(rtt)
            m.steps += 1
            last = s["t"] == last_t
            m.result_sent[(run_id, s["t"])] = time.perf_counter()
            await call.write(pb.VehicleMsg(result=pb.Result(
                t=s["t"], reward=s["reward"], terminated=s["terminated"] and last, truncated=s["truncated"] and last,
                crashed=s["crashed"], rtt_ms=rtt, final_poses=_poses(rec.end.get("finalPoses")) if last else [])))
            if period and not last:
                await asyncio.sleep(max(0.0, period - (time.perf_counter() - step_start)))
        summary = await call.read()
        if summary.WhichOneof("msg") == "summary" and summary.summary.status == "finished":
            m.finished += 1
        else:
            m.failed += 1
            m.failures.append(f"{summary.summary.status} {summary.summary.reason}")
        await call.done_writing()
        return True
    except grpc.aio.AioRpcError as e:
        m.failed += 1
        m.failures.append(f"rpc {e.code().name}")
        return True
    finally:
        if viewers:
            await asyncio.wait(viewers, timeout=30)
            for v in viewers:
                v.cancel()


async def replay_vehicle(stub: rpc.GatewayStub, rec: Recording, phase: Phase, m: Metrics,
                         session: aiohttp.ClientSession, http: str, deadline: float) -> None:
    """Start runs one after another until the phase's duration is over (a started run always completes)."""
    while time.perf_counter() < deadline:
        admitted = await replay_once(stub, rec, phase, m, session, http)
        if not admitted:  # refused: back off a little, then try again
            await asyncio.sleep(1.0 / phase.pace_hz if phase.pace_hz > 0 else 0.2)


async def sample_load(session: aiohttp.ClientSession, http: str, m: Metrics, every: float) -> None:
    while True:
        try:
            async with session.get(f"{http}/load") as r:
                m.load.append({"t": time.time(), **(await r.json())})
        except aiohttp.ClientError:
            pass
        await asyncio.sleep(every)


async def run_phase(gateway: str, http: str, rec: Recording, phase: Phase, sample_every: float) -> dict[str, Any]:
    m = Metrics()
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0)) as session:
        runs = phase.runs
        if isinstance(runs, str):  # "2x": a multiple of the gateway's run limit
            async with session.get(f"{http}/load") as r:
                limit = (await r.json())["maxRuns"]
            runs = int(round(float(runs.rstrip("x")) * limit))
        channel = grpc.aio.insecure_channel(gateway, options=KEEPALIVE_OPTIONS)
        stub = rpc.GatewayStub(channel)
        sampler = asyncio.create_task(sample_load(session, http, m, sample_every))
        start = time.perf_counter()
        deadline = start + phase.duration_s
        await asyncio.gather(*(replay_vehicle(stub, rec, phase, m, session, http, deadline) for _ in range(runs)))
        elapsed = time.perf_counter() - start
        sampler.cancel()
        await channel.close()
    return {
        "phase": phase.label(), "runs": runs, "attack": phase.attack, "epsilon": phase.epsilon,
        "paceHz": phase.pace_hz, "viewersPerRun": phase.viewers_per_run, "durationS": phase.duration_s,
        "elapsedS": elapsed, "steps": m.steps, "stepsPerSecond": m.steps / elapsed if elapsed else 0.0,
        "startedRuns": m.started, "finishedRuns": m.finished, "failedRuns": m.failed, "refused": m.refused,
        "failures": m.failures[:50], "viewerErrors": m.viewer_errors,
        "rttMs": percentiles(m.rtt_ms), "viewerDelayMs": percentiles(m.viewer_ms), "load": m.load,
        "runIds": m.run_ids,
    }


# ---------------------------------------------------------------- environment
def _cpu_model() -> str:
    host = os.environ.get("ICADS_HOST_CPU", "").strip()
    if host:
        return host
    try:
        if sys.platform == "darwin":
            return subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith(("model name", "hardware")):
                return line.split(":", 1)[1].strip()
        # ARM Linux (e.g. Docker Desktop's VM on a Mac) names only the vendor
        info = dict(line.split(":", 1) for line in subprocess.check_output(["lscpu"], text=True).splitlines()
                    if ":" in line)
        vendor, model = info.get("Vendor ID", "").strip(), info.get("Model name", "").strip()
        name = " ".join(x for x in (vendor, model) if x and x != "-")
        if name:
            return f"{name} (set ICADS_HOST_CPU for the host's model)"
    except (OSError, subprocess.CalledProcessError):
        pass
    return platform.processor() or "unknown"


def _cpu_limit() -> str:
    try:  # cgroup v2 (Docker)
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        return "none" if quota == "max" else f"{int(quota) / int(period):g} CPUs"
    except (OSError, ValueError):
        return "none"


def _get_json(url: str) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # /healthz answers 503 with the same body when a replica is down
        return json.loads(e.read())


def environment(http: str) -> dict[str, Any]:
    getaffinity = getattr(os, "sched_getaffinity", None)  # Linux only
    affinity = len(getaffinity(0)) if getaffinity else os.cpu_count()
    try:
        replicas = _get_json(f"{http}/healthz").get("replicas", {})
    except OSError:
        replicas = {}
    return {
        "hardware": {"cpu": _cpu_model(), "machine": platform.machine(), "system": platform.platform()},
        "cores": os.cpu_count(), "coresAvailableToLoadgen": affinity,
        "cpuLimit": {"loadgen": _cpu_limit(), "services": service_cpu_limit()},
        "replicas": replicas,  # as the gateway resolves them
        "runtime": os.environ.get("ICADS_RUNTIME", "plain processes"),
        "loadGeneratorSharesMachine": True,
        "gateway": http,
        "imageId": image_identity(),
    }


def replicas_seen(http: str, run_ids: list[str], limit: int = 20) -> dict[str, list[str]]:
    """Replica ids that answered some of a phase's runs (read from their step logs)."""
    seen: dict[str, set[str]] = {"inference": set(), "perturbation": set()}
    for run_id in run_ids[:limit]:
        try:
            with urllib.request.urlopen(f"{http}/v1/runs/{run_id}/log", timeout=30) as r:
                lines = r.read().decode().splitlines()
        except OSError:
            continue
        for line in lines:
            x = json.loads(line)
            if x.get("type") == "step":
                seen["inference"].add(x["inferenceReplica"])
                if x.get("perturbationReplica"):
                    seen["perturbation"].add(x["perturbationReplica"])
    return {k: sorted(v) for k, v in seen.items()}


def run_profile(profile_path: str, gateway: str, http: str, out_root: str) -> int:
    profile = Profile.load(profile_path)
    if not profile.recording_path.exists():
        record(profile, gateway, http)
    rec = Recording.load(profile.recording_path)
    started = datetime.now(timezone.utc)
    phases = []
    for phase in profile.phases():
        print(f"phase {phase.label()} for {phase.duration_s:g} s", file=sys.stderr, flush=True)
        result = asyncio.run(run_phase(gateway, http, rec, phase, profile.load_sample_s))
        result["replicasSeen"] = replicas_seen(http, result["runIds"])
        phases.append(result)
        print(f"  {result['stepsPerSecond']:.1f} steps/s, rtt p95 {result['rttMs']['p95']:.1f} ms, "
              f"viewer p95 {result['viewerDelayMs']['p95']:.1f} ms, refused {result['refused']}, "
              f"failed {result['failedRuns']}", file=sys.stderr, flush=True)
    env = environment(http)
    out = Path(out_root)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{profile.name}-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps({"profile": profile.raw, "started": started.isoformat(),
                                "finished": datetime.now(timezone.utc).isoformat(), "environment": env,
                                "phases": phases}, indent=2, default=str))
    print(f"wrote {path}", file=sys.stderr)
    return 0 if all(p["failedRuns"] == 0 for p in phases) else 1
