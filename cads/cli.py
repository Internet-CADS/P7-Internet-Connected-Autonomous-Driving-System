"""Local launch and experiment entry points."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from urllib.request import urlopen

from cads.protocol import EPSILONS, FRAMES, METHODS, ModelId, RunConfig

DEFAULT_VEHICLE = "ws://127.0.0.1:8000/ws/vehicle"


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Enter a positive integer")
    return number


def add_episode(parser):
    parser.add_argument("--url", default=DEFAULT_VEHICLE)
    parser.add_argument("--group", choices=FRAMES, default="vanilla_dqn")
    parser.add_argument("--model-seed", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--method", choices=METHODS, default="random_linf")
    parser.add_argument("--epsilon", type=float, choices=EPSILONS, default=0)
    parser.add_argument("--steps", type=positive, default=60)


def config(args) -> RunConfig:
    return RunConfig(
        seed=args.seed,
        model=ModelId(group=args.group, seed=args.model_seed),
        method=args.method,
        epsilon=args.epsilon,
        max_steps=args.steps,
    )


def wait_ready(url: str, process: subprocess.Popen) -> None:
    deadline = monotonic() + 45
    while process.poll() is None and monotonic() < deadline:
        try:
            with urlopen(url, timeout=1):
                return
        except OSError:
            sleep(0.1)
    raise RuntimeError(f"Service did not start at {url}")


def start(args) -> None:
    if not args.models.is_dir():
        raise RuntimeError(f"Models checkout not found at {args.models}. Set CADS_MODELS or pass --models.")
    processes = []
    ports = range(args.inference_port, args.inference_port + args.replicas)
    command = [sys.executable, "-m", "cads.cli"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def launch(component: str, *options: str):
        process = subprocess.Popen(
            [*command, component, "--models", str(args.models.resolve()), *options],
            creationflags=flags,
        )
        processes.append(process)
        return process

    try:
        for port in ports:
            process = launch("inference", "--port", str(port))
            wait_ready(f"http://127.0.0.1:{port}/healthz", process)
        urls = ",".join(f"ws://127.0.0.1:{port}/ws/infer" for port in ports)
        gateway = launch(
            "gateway",
            "--port",
            str(args.port),
            "--replicas",
            urls,
            "--db",
            str(args.db),
            "--limit",
            str(args.limit),
            "--workers",
            str(args.workers),
        )
        base = f"http://127.0.0.1:{args.port}"
        wait_ready(f"{base}/healthz", gateway)
        print(f"Monitoring page: {base}", flush=True)
        reported = set()
        while gateway.poll() is None:
            for port, replica in zip(ports, processes[:-1], strict=True):
                if replica.poll() is not None and port not in reported:
                    print(f"Inference replica on port {port} exited", file=sys.stderr, flush=True)
                    reported.add(port)
            sleep(0.5)
        raise RuntimeError("The gateway exited")
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(prog="cads")
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser("start", help="Start the gateway and inference replicas")
    local.add_argument("--replicas", type=positive, default=1)
    local.add_argument("--inference-port", type=positive, default=8001)
    gateway = commands.add_parser("gateway", help="Serve the vehicle and monitoring interfaces")
    gateway.add_argument(
        "--replicas", default=os.environ.get("CADS_REPLICAS", "ws://127.0.0.1:8001/ws/infer")
    )
    inference = commands.add_parser("inference", help="Serve checkpoint inference")
    for service in (local, gateway, inference):
        service.add_argument("--models", type=Path, default=Path(os.environ.get("CADS_MODELS", "models")))
        service.add_argument("--port", type=positive, default=8001 if service is inference else 8000)
    for service in (gateway, inference):
        service.add_argument("--host", default="127.0.0.1")
    for service in (local, gateway):
        service.add_argument("--db", type=Path, default=Path("data/runs.sqlite"))
        service.add_argument("--limit", type=positive, default=50)
        service.add_argument("--workers", type=positive, default=2)
    episode = commands.add_parser("vehicle", help="Run one highway simulation")
    add_episode(episode)
    experiment = commands.add_parser("experiment", help="Run a robustness experiment manifest")
    experiment.add_argument("manifest", type=Path)
    experiment.add_argument("--output", type=Path, required=True)
    experiment.add_argument("--url", default=DEFAULT_VEHICLE)
    load = commands.add_parser("load", help="Measure gateway throughput and viewer delay")
    add_episode(load)
    load.add_argument("--runs", type=positive, default=50)
    load.add_argument("--viewers", type=int, default=5)
    load.add_argument("--rate", type=float, default=0, help="Maximum offers per second per client, or 0")
    load.add_argument("--simulate", action="store_true", help="Step actual highway environments")
    load.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        if args.command in {"gateway", "inference"}:
            import uvicorn

            if args.command == "gateway":
                from cads.gateway import create_app

                app = create_app(
                    args.models.resolve(), args.db, args.replicas.split(","), args.limit, args.workers
                )
            else:
                from cads.inference import create_app

                app = create_app(args.models.resolve())
            uvicorn.run(
                app,
                host=args.host,
                port=args.port,
                access_log=False,
                ws_max_size=65536,
                ws="websockets-sansio",
            )
        elif args.command == "start":
            start(args)
        elif args.command == "vehicle":
            from cads.vehicle import run_episode

            print(json.dumps(asyncio.run(run_episode(args.url, config(args)))))
        elif args.command == "experiment":
            from cads.experiments import run_grid

            asyncio.run(run_grid(args.manifest, args.output, args.url))
        elif args.command == "load":
            from cads.load import measure

            asyncio.run(measure(args, config(args)))
    except KeyboardInterrupt:
        return
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
