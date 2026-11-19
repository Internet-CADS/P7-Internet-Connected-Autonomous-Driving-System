# Internet-Connected Autonomous Driving System

Run seeded `highway-v0` simulations against pretrained policies on one CPU-only machine. Vehicle clients and inference replicas exchange JSON messages with the gateway over WebSocket. The monitoring page shows live and recorded runs, including vehicle positions, clean and perturbed observations, actions and Q-values.

## Start locally

Use the team's authorised [models checkout](https://github.com/Internet-CADS/models), pinned at `64a52648080ad5d4aa52bfa48ebdcf89356a5241`. Place it in the ignored `models/` directory, or set `CADS_MODELS` to its root. The loader reads the rev3 files under `entries/05_wocar_dqn_r3/artifacts/run_2`.

With Docker running, start the gateway, inference and a demo episode.

```sh
docker compose up --build
```

Open [the local monitor](http://127.0.0.1:8000). Choose the demo run to replay it or download its JSON Lines log. The demo client exits after its episode. Start another one with `docker compose run --rm demo`.

For plain processes, install Python 3.13 and uv, then start the services.

```sh
uv sync --locked
uv run --locked cads start --replicas 2
```

In another terminal, send an episode through the services.

```sh
uv run --locked cads vehicle --group wocar_dqn --model-seed 1 --seed 42 --method pgd --epsilon 0.01
```

Use `--port` on `cads start` to change the gateway port, and `--url ws://127.0.0.1:PORT/ws/vehicle` on clients. The plain-process gateway writes to `data/runs.sqlite`. Compose uses the `runs` volume.

## Test and reproduce

```sh
uv run --locked ruff check .
uv run --locked pytest -q
uv run --locked cads experiment experiments/smoke.json --output data/smoke
uv run --locked cads load --runs 50 --viewers 5 --steps 20 --output data/load.json
```

Tests create their own fixture weights. The smoke grid and load commands use the running services and the real checkpoints. The experiment runner saves the manifest, episode results, seed-level aggregate statistics and a plot. Use `experiments/reduced.json` for the full reduced grid of 15,300 episodes. Choose a fresh output directory for each experiment. The load runner uses synthetic observations by default. Add `--simulate` to include simulator stepping, or use `--rate 1` to pace the generated requests. Repeat the same workload with different `cads start --replicas` values to compare scaling.

[Specification](docs/SPEC.md) · [Decisions](docs/DECISIONS.md) · [Implementation plan](docs/PLAN.md) · [Feasibility](docs/FEASIBILITY.md)
