"""`icads experiment <grid file>`: the robustness experiment, run through the service (docs/ARCHITECTURE.md, section 10).

1. Plays every episode of the grid with parallel vehicle-client worker processes (`icads vehicle --jobs`,
   unpaced), which take episodes from a shared queue.
2. Fetches the results through the gateway's REST API, never from the SQLite file (D15).
3. Writes Parquet tables, plots and a manifest to `<out>/<experiment label>/`.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from icads.experiments.grid import Episode, Grid, plan
from icads.experiments.workers import VehiclePool
from icads.protocol.runtime import git_commit, image_identity

Outcome = dict[str, Any]
PlayFn = Callable[[Episode, int], Outcome]


# ---------------------------------------------------------------- playing
def episode_job(ep: Episode, experiment: str, max_steps: int, attempt: int) -> dict[str, Any]:
    """The job line for one episode (see `icads vehicle --jobs`). Unpaced, labelled for the experiment."""
    return {"model": ep.model_id, "seed": ep.env_seed, "attack": ep.attack, "epsilon": ep.epsilon,
            "maxSteps": max_steps, "paceHz": 0,
            "labels": {"experiment": experiment, "cell": ep.cell, "episode": str(ep.env_seed),
                       "attempt": str(attempt)}}


def play_all(episodes: list[Episode], play: PlayFn, workers: int,
             progress: Callable[[int, int], None] | None = None) -> tuple[list[Outcome], int]:
    """Play every episode; a failed one is played once more with the same settings.
    Returns every outcome (both attempts) and the number of failed attempts."""
    outcomes: list[Outcome] = []
    failures = 0
    done_count = 0

    def record(ep: Episode, attempt: int, out: Outcome) -> bool:
        nonlocal failures, done_count
        outcomes.append({**out, "episode": ep, "attempt": attempt})
        ok = out.get("status") == "finished"
        if not ok:
            failures += 1
        if ok or attempt == 2:
            done_count += 1
            if progress:
                progress(done_count, len(episodes))
        return ok

    if workers <= 1:
        for ep in episodes:
            if not record(ep, 1, play(ep, 1)):
                record(ep, 2, play(ep, 2))
        return outcomes, failures

    # Worker threads take episodes from the pool's shared queue; each drives its own vehicle process.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: dict[Future, tuple[Episode, int]] = {pool.submit(play, ep, 1): (ep, 1) for ep in episodes}
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for f in finished:
                ep, attempt = pending.pop(f)
                try:
                    out = f.result()
                except Exception as e:  # noqa: BLE001  (a crashed worker counts as a failed episode)
                    out = {"runId": "", "status": "error", "reason": "WORKER_ERROR", "message": str(e)}
                if not record(ep, attempt, out) and attempt == 1:
                    pending[pool.submit(play, ep, 2)] = (ep, 2)
    return outcomes, failures


# ---------------------------------------------------------------- fetching (REST only)
def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode())


def fetch_runs(http: str, experiment: str, page: int = 1000) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    while True:
        q = urllib.parse.urlencode({"experiment": experiment, "limit": page, "offset": len(runs), "synthetic": "all"})
        batch = _get_json(f"{http}/v1/runs?{q}")["runs"]
        runs += batch
        if len(batch) < page:
            return runs


def fetch_steps(http: str, run_id: str) -> list[dict[str, Any]]:
    with urllib.request.urlopen(f"{http}/v1/runs/{urllib.parse.quote(run_id)}/log", timeout=60) as r:
        lines = [json.loads(line) for line in r.read().decode().splitlines() if line]
    return [x for x in lines if x.get("type") == "step"]


def runs_table(http: str, experiment: str) -> pd.DataFrame:
    rows = []
    for run in fetch_runs(http, experiment):
        group, checkpoint = run["modelId"].split("/")
        steps = fetch_steps(http, run["runId"])
        flips = sum(1 for s in steps if s["action"] != s["cleanAction"])
        labels = run["labels"]
        rows.append(dict(
            run_id=run["runId"], group=group, checkpoint=int(checkpoint), model_id=run["modelId"],
            attack=run["attack"], epsilon=float(run["epsilon"]), cell=labels.get("cell"),
            env_seed=int(run["seed"]), attempt=int(labels.get("attempt", 1)), status=run["status"],
            reason=run["reason"], steps=int(run["steps"]), total_reward=float(run["totalReward"]),
            crashed=bool(run["crashed"]), flips=flips, flip_rate=flips / max(1, len(steps)),
            mean_rtt_ms=float(np.mean([s["rttMs"] for s in steps])) if steps else float("nan")))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["group", "checkpoint", "attack", "epsilon", "env_seed", "attempt"]).reset_index(drop=True)


# ---------------------------------------------------------------- summary
CONDITION = ["group", "attack", "epsilon", "cell"]
METRICS = ["crash_rate", "mean_return", "episode_length", "flip_rate"]


def summarise(runs: pd.DataFrame) -> pd.DataFrame:
    """Per condition: each metric as the mean over checkpoint seeds, with the sample standard deviation
    over checkpoint seeds as its spread. Runs that did not finish are excluded and counted."""
    excluded = runs[runs["status"] != "finished"].groupby(CONDITION).agg(excluded=("status", "size"))
    ok = runs[runs["status"] == "finished"]
    per_seed = ok.groupby(CONDITION + ["checkpoint"]).agg(
        crash_rate=("crashed", "mean"), mean_return=("total_reward", "mean"), episode_length=("steps", "mean"),
        flips=("flips", "sum"), step_count=("steps", "sum"), episodes=("crashed", "size")).reset_index()
    per_seed["flip_rate"] = per_seed["flips"] / per_seed["step_count"].clip(lower=1)
    g = per_seed.groupby(CONDITION)
    out = pd.DataFrame(g[METRICS].mean())
    spread = pd.DataFrame(g[METRICS].std(ddof=1)).fillna(0.0).add_suffix("_std")
    counts = pd.DataFrame(g.agg(episodes=("episodes", "sum"), checkpoints=("checkpoint", "nunique")))
    table = out.join(spread).join(counts).join(pd.DataFrame(excluded), how="outer").reset_index()
    table["excluded"] = table["excluded"].fillna(0).astype(int)
    for c in ("episodes", "checkpoints"):
        table[c] = table[c].fillna(0).astype(int)
    cols = CONDITION + [x for m in METRICS for x in (m, f"{m}_std")] + ["episodes", "checkpoints", "excluded"]
    return pd.DataFrame(table[cols]).sort_values(by=["group", "attack", "epsilon"]).reset_index(drop=True)


def plot(conditions: pd.DataFrame, out: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    written = []
    attacks = sorted(a for a in conditions["attack"].unique() if a != "none")
    for metric, label in (("crash_rate", "Crash rate"), ("mean_return", "Mean return"), ("flip_rate", "Action-flip rate")):
        fig, axes = plt.subplots(1, max(1, len(attacks)), figsize=(4.2 * max(1, len(attacks)), 3.4), sharey=True,
                                 squeeze=False)
        for ax, attack in zip(axes[0], attacks or ["none"]):
            for group in sorted(conditions["group"].unique()):
                mask = (conditions["group"] == group) & conditions["attack"].isin(["none", attack])
                rows = pd.DataFrame(conditions[mask]).sort_values(by="epsilon")
                ax.errorbar(rows["epsilon"], rows[metric], yerr=rows[f"{metric}_std"], marker="o", capsize=3,
                            label=group)
            ax.set_title(attack)
            ax.set_xlabel("ε")
            ax.grid(alpha=0.3)
        axes[0][0].set_ylabel(label)
        axes[0][-1].legend(fontsize=8)
        fig.tight_layout()
        path = out / f"{metric}.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        written.append(path.name)
    return written


def run_experiment(grid_path: str, gateway: str, http: str, out_root: str, label: str | None = None,
                   play: PlayFn | None = None) -> int:
    grid = Grid.load(grid_path)
    started = datetime.now(timezone.utc)
    label = label or f"{grid.experiment}-{started.strftime('%Y%m%dT%H%M%SZ')}"
    episodes = plan(grid)
    print(f"experiment {label}: {len(episodes)} episodes, {grid.workers} workers", file=sys.stderr, flush=True)
    pool: VehiclePool | None = None
    if play is None:
        vehicles = pool = VehiclePool(gateway)

        def play_in_vehicle(ep: Episode, attempt: int) -> Outcome:
            return vehicles.play(episode_job(ep, label, grid.max_steps, attempt))

        play = play_in_vehicle
    t0 = time.monotonic()

    def progress(done: int, total: int) -> None:
        if done == total or done % max(1, total // 20) == 0:
            print(f"  {done}/{total} episodes ({time.monotonic() - t0:.0f} s)", file=sys.stderr, flush=True)

    try:
        outcomes, failures = play_all(episodes, play, grid.workers, progress)
    finally:
        if pool is not None:
            pool.close()
    lost = sum(1 for o in outcomes if o["attempt"] == 2 and o.get("status") != "finished")
    runs = runs_table(http, label)
    out = Path(out_root) / label
    out.mkdir(parents=True, exist_ok=True)
    conditions = summarise(runs) if not runs.empty else pd.DataFrame()
    runs.to_parquet(out / "runs.parquet", index=False)
    conditions.to_parquet(out / "conditions.parquet", index=False)
    conditions.to_csv(out / "conditions.csv", index=False)
    plots = plot(conditions, out) if not conditions.empty else []
    manifest = {
        "experiment": label, "grid": grid.as_dict(), "gridFile": str(grid_path), "commit": git_commit(),
        "imageId": image_identity(),
        "started": started.isoformat(), "finished": datetime.now(timezone.utc).isoformat(),
        "episodes": len(episodes), "runs": int(len(runs)), "failedAttempts": failures, "lostEpisodes": lost,
        "excludedRuns": int(np.sum(runs["status"].to_numpy() != "finished")) if not runs.empty else 0,
        "files": ["runs.parquet", "conditions.parquet", "conditions.csv", *plots],
        "metrics": "per condition: mean over checkpoint seeds of the per-seed crash rate, mean return, episode "
                   "length and action-flip rate; *_std is the sample standard deviation over checkpoint seeds",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print(conditions[CONDITION + METRICS].to_string(index=False) if not conditions.empty else "no results")
    print(f"wrote {out}", file=sys.stderr)
    if failures:
        print(f"{failures} failed attempts, {lost} episodes failed twice", file=sys.stderr)
    return 0 if lost == 0 else 1
