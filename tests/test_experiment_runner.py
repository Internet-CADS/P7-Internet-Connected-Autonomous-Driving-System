"""Issue 09: the experiment runner's grid, retries and summary."""

import json

import pandas as pd
import pytest

from icads.experiments.grid import Grid, plan
from icads.experiments.runner import play_all, summarise


def grid(**over):
    base = dict(experiment="t", groups=["vanilla_dqn", "wocar_dqn"], seeds=[1, 2], attacks=["pgd", "minbest"],
                epsilons=[0.0, 0.005, 0.01], episodes=3, workers=2)
    base.update(over)
    return Grid.from_dict(base)


def test_grid_file_round_trip(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"experiment": "e1", "groups": ["sa_dqn"], "seeds": [1], "attacks": ["pgd"],
                             "epsilons": [0, 0.01], "episodes": 2, "workers": 1}))
    g = Grid.load(p)
    assert (g.experiment, g.groups, g.seeds, g.attacks, g.epsilons, g.episodes, g.workers) == \
        ("e1", ["sa_dqn"], [1], ["pgd"], [0.0, 0.01], 2, 1)


@pytest.mark.parametrize("bad", [dict(groups=["nope"]), dict(seeds=[8]), dict(attacks=["fgsm"]),
                                 dict(epsilons=[-1]), dict(episodes=0)])
def test_bad_grid_is_rejected(bad):
    with pytest.raises(ValueError):
        grid(**bad)


def test_plan_runs_clean_once_per_model_and_shares_env_seeds():
    eps = plan(grid())
    # per model: 1 clean + 2 attacks x 2 non-zero epsilons = 5 conditions, x 3 episodes
    assert len(eps) == 2 * 2 * 5 * 3
    clean = [e for e in eps if e.cell == "clean"]
    assert len(clean) == 2 * 2 * 3 and all(e.attack == "none" and e.epsilon == 0.0 for e in clean)
    by_cond = {}
    for e in eps:
        by_cond.setdefault((e.model_id, e.cell), []).append(e.env_seed)
    assert all(seeds == [0, 1, 2] for seeds in by_cond.values())
    assert {e.cell for e in eps} == {"clean", "pgd-0.005", "pgd-0.01", "minbest-0.005", "minbest-0.01"}


def test_a_failed_episode_is_run_once_more():
    calls = []

    def play(ep, attempt):
        calls.append((ep.env_seed, attempt))
        ok = not (ep.env_seed == 1 and attempt == 1)
        return {"status": "finished" if ok else "error", "runId": f"r{len(calls)}"}

    eps = plan(grid(groups=["sa_dqn"], seeds=[1], attacks=["pgd"], epsilons=[0.01], episodes=3))
    outcomes, failures = play_all(eps, play, workers=1)
    assert sorted(calls) == [(0, 1), (1, 1), (1, 2), (2, 1)]
    assert failures == 1 and len(outcomes) == 4


def test_summary_is_the_mean_over_checkpoint_seeds_with_spread():
    rows = []
    # checkpoint 1 crashes in 1 of 2 episodes, checkpoint 2 in 2 of 2
    for seed, crashed, ret, length, flips in [(1, True, 1.0, 10, 2), (1, False, 3.0, 20, 0),
                                              (2, True, 2.0, 10, 5), (2, True, 2.0, 10, 5)]:
        rows.append(dict(group="sa_dqn", checkpoint=seed, attack="pgd", epsilon=0.01, cell="pgd-0.01",
                         status="finished", crashed=crashed, total_reward=ret, steps=length, flips=flips))
    rows.append(dict(group="sa_dqn", checkpoint=1, attack="pgd", epsilon=0.01, cell="pgd-0.01", status="error",
                     crashed=True, total_reward=-99.0, steps=1, flips=1))
    out = summarise(pd.DataFrame(rows))
    r = out.iloc[0]
    assert r["crash_rate"] == pytest.approx(0.75) and r["crash_rate_std"] == pytest.approx(0.5 ** 0.5 / 2)  # sample std of 0.5 and 1.0
    assert r["mean_return"] == pytest.approx(2.0) and r["mean_return_std"] == pytest.approx(0.0)
    assert r["episode_length"] == pytest.approx(12.5)
    # flip rate per checkpoint: (2+0)/(10+20) and 10/20 -> mean of 1/15 and 1/2
    assert r["flip_rate"] == pytest.approx((1 / 15 + 0.5) / 2)
    assert r["episodes"] == 4 and r["checkpoints"] == 2 and r["excluded"] == 1
