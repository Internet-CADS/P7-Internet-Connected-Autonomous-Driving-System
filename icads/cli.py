"""One entry point: `icads <command>`.

Each command imports its component lazily, so `icads vehicle` never loads PyTorch.
"""

from __future__ import annotations

import argparse
import sys


def _labels(values: list[str]) -> dict[str, str]:
    out = {}
    for v in values:
        key, sep, val = v.partition("=")
        if not sep or not key:
            raise argparse.ArgumentTypeError(f"--label must be key=value, got {v!r}")
        out[key] = val
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="icads")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("inference", help="serve the agent (Act, ListModels)")
    s.add_argument("--port", type=int, default=50052)
    s.add_argument("--replica-id")

    s = sub.add_parser("perturbation", help="serve the attacks (Perturb)")
    s.add_argument("--port", type=int, default=50053)
    s.add_argument("--replica-id")

    s = sub.add_parser("gateway", help="run the gateway (gRPC vehicle link, REST, SSE, page)")
    s.add_argument("--grpc-port", type=int, default=50051)
    s.add_argument("--http-port", type=int, default=8000)
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--inference", help="gRPC target(s) of the inference service (env ICADS_INFERENCE)")
    s.add_argument("--perturbation", help="gRPC target(s) of the perturbation service (env ICADS_PERTURBATION)")
    s.add_argument("--db", help="SQLite file (env ICADS_DB, default data/runs.sqlite)")
    s.add_argument("--max-runs", type=int, help="active run limit (env ICADS_MAX_RUNS, default 64)")

    s = sub.add_parser("vehicle", help="play one episode through the gateway")
    s.add_argument("--gateway", default="127.0.0.1:50051")
    s.add_argument("--model", help="model id, e.g. wocar_dqn/1 (required unless --jobs)")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--attack", default="none")
    s.add_argument("--epsilon", type=float, default=0.0)
    s.add_argument("--pace", type=float, default=0.0, help="steps per second; 0 = unpaced")
    s.add_argument("--max-steps", type=int, default=60)
    s.add_argument("--label", action="append", default=[], help="key=value, repeatable")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--jobs", action="store_true",
                   help="worker mode: read one JSON job per line from stdin, write one outcome line per job")

    s = sub.add_parser("demo", help="three paced vehicles side by side")
    s.add_argument("--gateway", default="127.0.0.1:50051")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--pace", type=float, default=1.0)
    s.add_argument("--loop", action="store_true", help="start new episodes forever")

    s = sub.add_parser("experiment", help="run a robustness grid through the service")
    s.add_argument("grid")
    s.add_argument("--gateway", default="127.0.0.1:50051")
    s.add_argument("--http", default="http://127.0.0.1:8000")
    s.add_argument("--out", default="data/experiments")

    s = sub.add_parser("loadgen", help="scalability and quality-of-service tests")
    s.add_argument("profile")
    s.add_argument("--gateway", default="127.0.0.1:50051")
    s.add_argument("--http", default="http://127.0.0.1:8000")
    s.add_argument("--out", default="data/loadtests")

    s = sub.add_parser("health", help="exit 0 if a gRPC health check at TARGET reports SERVING")
    s.add_argument("target")

    a = p.parse_args(argv)

    if a.command == "inference":
        from icads.inference.service import serve
        from icads.protocol.serving import wait_forever
        server, port = serve(a.port, a.replica_id)
        print(f"inference listening on {port}", flush=True)
        wait_forever(server)
    elif a.command == "perturbation":
        from icads.perturbation.service import serve
        from icads.protocol.serving import wait_forever
        server, port = serve(a.port, a.replica_id)
        print(f"perturbation listening on {port}", flush=True)
        wait_forever(server)
    elif a.command == "gateway":
        from icads.gateway.app import main as gateway_main
        gateway_main(a)
    elif a.command == "vehicle":
        from icads.vehicle.client import RunSettings, run_vehicle, serve_jobs
        if a.jobs:
            return serve_jobs(a.gateway, sys.stdin, sys.stdout)
        if not a.model:
            p.error("vehicle: --model is required")
        settings = RunSettings(model_id=a.model, seed=a.seed, attack=a.attack, epsilon=a.epsilon,
                               pace_hz=a.pace, max_steps=a.max_steps, labels=_labels(a.label))
        summary = run_vehicle(a.gateway, settings, verbose=not a.quiet)
        print(summary.as_line(), flush=True)
        return 0 if summary.status == "finished" else 1
    elif a.command == "demo":
        from icads.vehicle.demo import run_demo
        return run_demo(a.gateway, a.seed, a.pace, a.loop)
    elif a.command == "experiment":
        from icads.experiments.runner import run_experiment
        return run_experiment(a.grid, a.gateway, a.http, a.out)
    elif a.command == "loadgen":
        from icads.loadgen.runner import run_profile
        return run_profile(a.profile, a.gateway, a.http, a.out)
    elif a.command == "health":
        from icads.protocol.serving import check_health
        return 0 if check_health(a.target) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
