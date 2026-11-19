"""Run paired robustness grids through the service and export report data."""

import asyncio
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Annotated
from urllib.parse import urlsplit
from urllib.request import urlopen

from pydantic import Field, model_validator
from websockets.exceptions import WebSocketException

from cads.protocol import Attack, Budget, EpisodeSeed, Group, Message, ModelId, RunConfig
from cads.vehicle import RunError, run_episode


class Manifest(Message):
    groups: Annotated[list[Group], Field(min_length=1)]
    model_seeds: Annotated[list[Annotated[int, Field(ge=1, le=7)]], Field(min_length=1)]
    methods: Annotated[list[Attack], Field(min_length=1)]
    epsilons: Annotated[list[Budget], Field(min_length=1)]
    episodes: Annotated[int, Field(ge=1)]
    episode_seed_start: EpisodeSeed = 0
    max_steps: Annotated[int, Field(ge=1, le=60)] = 60

    @model_validator(mode="after")
    def distinct(self):
        for values in (self.groups, self.model_seeds, self.methods, self.epsilons):
            if len(set(values)) != len(values):
                raise ValueError("Grid values must be unique")
        if self.episode_seed_start + self.episodes > 2**32:
            raise ValueError("Episode seed range is too large")
        return self


def cases(manifest: Manifest):
    for group in manifest.groups:
        for model_seed in manifest.model_seeds:
            for epsilon in manifest.epsilons:
                methods = manifest.methods if epsilon else manifest.methods[:1]
                for method in methods:
                    for offset in range(manifest.episodes):
                        yield RunConfig(
                            seed=manifest.episode_seed_start + offset,
                            model=ModelId(group=group, seed=model_seed),
                            method=method,
                            epsilon=epsilon,
                            max_steps=manifest.max_steps,
                        )


def download_records(url: str, run_id: str) -> list[dict]:
    parsed = urlsplit(url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    with urlopen(f"{scheme}://{parsed.netloc}/runs/{run_id}/log", timeout=10) as response:
        return [json.loads(line) for line in response]


def summarize(rows: list[dict]) -> list[dict]:
    metrics = {
        "crash_rate": "crashed",
        "return": "total_reward",
        "episode_length": "steps",
        "action_flip_rate": "action_flip_rate",
    }
    by_seed = defaultdict(list)
    for row in rows:
        by_seed[row["group"], row["method"], row["epsilon"], row["model_seed"]].append(row)
    by_condition = defaultdict(list)
    for (group, method, epsilon, _), episodes in by_seed.items():
        values = {label: mean(row[field] for row in episodes) for label, field in metrics.items()}
        by_condition[group, method, epsilon].append((len(episodes), values))
    summary = []
    for (group, method, epsilon), seeds in sorted(by_condition.items()):
        row = {
            "group": group,
            "method": method,
            "epsilon": epsilon,
            "model_seeds": len(seeds),
            "episodes": sum(n for n, _ in seeds),
        }
        for metric in metrics:
            values = [seed[metric] for _, seed in seeds]
            row[f"{metric}_mean"] = mean(values)
            row[f"{metric}_seed_sd"] = stdev(values) if len(values) > 1 else None
        summary.append(row)
    return summary


def plot(summary: list[dict], destination: Path, methods: list[str]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    figure, axes = plt.subplots(1, len(methods), figsize=(4 * len(methods), 4), squeeze=False, sharey=True)
    groups = sorted({row["group"] for row in summary})
    for axis, method in zip(axes[0], methods, strict=True):
        for group in groups:
            points = [row for row in summary if row["group"] == group and row["method"] in ("clean", method)]
            points.sort(key=lambda row: row["epsilon"])
            axis.errorbar(
                [row["epsilon"] for row in points],
                [row["crash_rate_mean"] * 100 for row in points],
                yerr=[(row["crash_rate_seed_sd"] or 0) * 100 for row in points],
                marker="o",
                capsize=3,
                label=group,
            )
        axis.set(title=method, xlabel="L∞ budget ε", ylim=(0, 100))
        axis.grid(alpha=0.25)
    axes[0][0].set_ylabel("Episodes with collision (%)")
    axes[0][-1].legend(loc="best")
    figure.tight_layout()
    figure.savefig(destination, dpi=180)
    plt.close(figure)


async def run_grid(manifest_path: Path, output: Path, url: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    (output / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    rows, failures = [], []
    with (output / "episodes.jsonl").open("w", encoding="utf-8") as episode_log:
        for config in cases(manifest):
            identity = {
                "group": config.model.group,
                "model_seed": config.model.seed,
                "frame": config.model.frame,
                "episode_seed": config.seed,
                "method": config.method if config.epsilon else "clean",
                "epsilon": config.epsilon,
            }
            try:
                result = await run_episode(url, config)
                records = await asyncio.to_thread(download_records, url, result["run_id"])
                if len(records) != result["steps"]:
                    raise RunError("Gateway log is incomplete")
                result["action_flip_rate"] = mean(row["action"] != row["clean_action"] for row in records)
                result["mean_rtt_ms"] = mean(result.pop("rtt_ms"))
                row = {**identity, **result, "status": "completed"}
                rows.append(row)
            except (RunError, OSError, WebSocketException) as exc:
                row = {**identity, "status": "failed", "error": str(exc)}
                failures.append(row)
            episode_log.write(json.dumps(row) + "\n")
            episode_log.flush()

    if not rows:
        raise RuntimeError("No episodes completed. See episodes.jsonl.")
    pq.write_table(pa.Table.from_pylist(rows), output / "episodes.parquet")
    summary = summarize(rows)
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    plot(summary, output / "crashes.png", manifest.methods)
    print(f"{len(rows)} completed, {len(failures)} failed. Results in {output}", flush=True)
