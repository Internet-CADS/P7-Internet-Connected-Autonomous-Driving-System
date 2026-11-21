# P7-Internet-Connected-Autonomous-Driving-System

- Specification: [`docs/SPEC.md`](docs/SPEC.md). A short form (v0.7): what is decided, plus the questions still open with the supervisor (section 7).
- Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The agreed system architecture, with its decision log.

## Start everything with one command (Docker)

```bash
docker compose up --build
```

This starts the gateway, 2 inference replicas and 2 perturbation replicas from one image (`icads`), which holds the package, the page and the 21 checkpoints. Open http://127.0.0.1:8000 for the monitoring page. Only the gateway's ports are published, and only on 127.0.0.1: HTTP on 8000 and the vehicle link (gRPC) on 50051. The store and the experiment output go to `./data`.

- Demo: `docker compose --profile demo up` adds three vehicles at 1 step per second on the same seed: vanilla DQN without an attack, vanilla DQN under PGD at ε = 0.01, and WocaR-DQN under PGD at ε = 0.01. Watch them side by side in the page (one tab per run).
- One vehicle: `docker compose run --rm vehicle --model wocar_dqn/1 --seed 7 --attack pgd --epsilon 0.01 --pace 1`
- Experiment: `docker compose run --rm experiments experiments/smoke.json` (results in `data/experiments/`)
- Load test: `docker compose run --rm loadgen experiments/loadgen/capacity.json` (results in `data/loadtests/`)
- More replicas: `docker compose up --scale perturbation=4`
- Failure test: `scripts/failure_test.sh`

The image sets PyTorch and the maths libraries to one thread, and each replica is limited to one CPU.

**Load balancing.** The gateway reaches each service through one gRPC channel (`dns:///inference:50052`, `dns:///perturbation:50053`) with gRPC's `round_robin` policy over every address Docker's DNS returns. This was checked with Docker Desktop on an Apple M3: the steps of one run were answered by both replicas of each service, and after `--scale perturbation=4` by all four. The stored replica ids (`inferenceReplica`, `perturbationReplica` in each step of `/v1/runs/{runId}/log`) show which replicas answered. `/healthz` checks every replica the gateway resolves and reports how many there are. The services close each connection after about 30 s, so replicas added with `--scale` while the gateway runs are used within about 40 s. The fallback (one channel per named replica, `ICADS_BALANCE=per-replica`, commented in `compose.yaml`) is not needed with Docker Desktop.

**One-off tools need the stack to be up.** `docker compose run` for `vehicle`, `experiments` and `loadgen` does not start the other services, on purpose: if it did, it would put them back to their declared replica counts and undo a `--scale`.

**Result files.** Load-test results record the replica counts the gateway sees and the image's build id. Inside Docker the CPU model is not visible (only "Apple" on a Mac), so pass it in: `ICADS_HOST_CPU="$(sysctl -n machdep.cpu.brand_string)" docker compose run --rm loadgen ...`.

**Stopping.** `docker compose down` stops everything. Active runs end as `error` with reason `GATEWAY_RESTART`, and their vehicles are told. The store in `./data` is kept.

## Development: plain processes

Requires [uv](https://docs.astral.sh/uv/). Dependencies are pinned in `uv.lock`.

```bash
uv sync
```

Start each in its own terminal:

```bash
uv run icads inference --port 50061
```

```bash
uv run icads perturbation --port 50071
```

```bash
uv run icads gateway --inference 127.0.0.1:50061 --perturbation 127.0.0.1:50071
```

More replicas: start more services on other ports and list them, for example `--perturbation 127.0.0.1:50071,127.0.0.1:50072`. Then play an episode and open http://127.0.0.1:8000:

```bash
uv run icads vehicle --model wocar_dqn/1 --seed 7 --attack pgd --epsilon 0.01 --pace 1
```

Other commands: `icads demo`, `icads experiment <grid file>`, `icads loadgen <profile file>`, `icads health <host:port>`. `icads vehicle --jobs` is a worker mode that plays one episode per JSON line on stdin; the experiment runner starts its workers this way, so it never imports the vehicle client. Gateway settings can also come from the environment: `ICADS_INFERENCE`, `ICADS_PERTURBATION`, `ICADS_DB` (default `data/runs.sqlite`), `ICADS_MAX_RUNS` (default 64).

## Tests

```bash
uv run pytest
```

```bash
uv run pyright
```

The page's overlay maths is tested with Node (`node --test "tests/web/*.test.mjs"`); pytest runs it when Node is installed. After changing a `.proto` file, regenerate the committed code with `scripts/gen_protos.sh`; CI fails if the committed code is out of date. `tests/fixtures/reference.json` holds Q-values and attack outputs recorded with the models repository's own code; `tests/fixtures/make_reference.py` re-creates it from a clone of that repository.

## Layout

- `icads/protocol/`: the `.proto` files, the generated gRPC code and the reference settings
- `icads/policy/`, `icads/attacks/`: the network and checkpoint loader, and the four attacks (ported from the models repository)
- `icads/gateway/`: run logic (`core.py`, no gRPC), gRPC adapter, REST and server-sent events, the SQLite store
- `icads/inference/`, `icads/perturbation/`: the two stateless services
- `icads/vehicle/`: the vehicle client and the demo (never imports PyTorch)
- `icads/experiments/`, `icads/loadgen/`: the experiment runner and the load generator
- `web/`: the monitoring page (plain JavaScript, no build step)
- `checkpoints/`: the 21 checkpoints and `SOURCE.md`
- `experiments/`: experiment grids and load-test profiles
