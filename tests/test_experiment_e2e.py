"""Issue 09 through the whole system: a smoke grid, run twice, gives identical result tables."""

import json

import pandas as pd
import pytest

from icads.experiments.runner import run_experiment
from tests.system import running_system

pytestmark = pytest.mark.slow


def test_smoke_grid_twice_gives_identical_tables(tmp_path):
    grid = tmp_path / "grid.json"
    grid.write_text(json.dumps({"experiment": "t", "groups": ["vanilla_dqn"], "seeds": [1, 2],
                                "attacks": ["pgd", "random_linf"], "epsilons": [0, 0.01], "episodes": 2,
                                "workers": 2, "maxSteps": 5}))
    with running_system(tmp_path / "sys") as system:
        out = tmp_path / "out"
        assert run_experiment(str(grid), system.grpc_target, system.http, str(out), label="a") == 0
        assert run_experiment(str(grid), system.grpc_target, system.http, str(out), label="b") == 0

        runs_a = pd.read_parquet(out / "a" / "runs.parquet")
        runs_b = pd.read_parquet(out / "b" / "runs.parquet")
        assert len(runs_a) == 2 * 3 * 2  # 2 checkpoints x (clean + 2 attacks) x 2 episodes
        assert set(runs_a["cell"]) == {"clean", "pgd-0.01", "random_linf-0.01"}
        drop = ["run_id", "mean_rtt_ms"]
        pd.testing.assert_frame_equal(runs_a.drop(columns=drop), runs_b.drop(columns=drop))
        pd.testing.assert_frame_equal(pd.read_parquet(out / "a" / "conditions.parquet"),
                                      pd.read_parquet(out / "b" / "conditions.parquet"))

        manifest = json.loads((out / "a" / "manifest.json").read_text())
        assert manifest["grid"]["experiment"] == "t" and manifest["commit"] and "imageId" in manifest
        assert (out / "a" / "crash_rate.png").exists()

        # every run carries the labels; the REST filter selects one experiment
        listed = system.get("/v1/runs?experiment=a&limit=100")["runs"]
        assert len(listed) == 12 and all({"experiment", "cell"} <= set(r["labels"]) for r in listed)


def test_runner_reads_results_only_through_rest():
    import icads.experiments.runner as runner
    source = open(runner.__file__).read()
    assert "sqlite3" not in source and "icads.gateway" not in source
