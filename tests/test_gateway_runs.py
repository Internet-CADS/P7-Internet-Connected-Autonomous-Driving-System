"""The gateway's run logic on plain data (no gRPC): one clean run, step order, storage."""

import asyncio

import numpy as np
import pytest

from icads.gateway.core import Gateway
from icads.gateway.model import ActionOut, SummaryOut, WelcomeOut
from icads.gateway.store import Store
from tests.gateway_helpers import FakeServices, hello, obs, result


@pytest.fixture
async def gw(tmp_path):
    store = Store(tmp_path / "runs.sqlite")
    store.start(asyncio.get_running_loop())
    g = Gateway(store, FakeServices(), max_runs=4)
    yield g
    store.close()


async def play(gw: Gateway, steps: int, **hello_over):
    welcome = await gw.on_hello(hello(**hello_over))
    assert isinstance(welcome, WelcomeOut)
    run_id = welcome.run_id
    actions = []
    summary = None
    for t in range(steps):
        out = await gw.on_obs(run_id, obs(t))
        assert isinstance(out, ActionOut) and out.t == t
        actions.append(out.action)
        summary = await gw.on_result(run_id, result(t, done=(t == steps - 1), final=(t == steps - 1)))
    return run_id, actions, summary


async def test_clean_run_is_stored_and_finishes(gw):
    run_id, actions, summary = await play(gw, 5)
    assert isinstance(summary, SummaryOut)
    assert (summary.status, summary.steps, summary.crashed) == ("finished", 5, False)
    assert summary.total_reward == pytest.approx(2.5)
    run = gw.store.read_run(run_id)
    assert run["status"] == "finished" and run["steps"] == 5 and run["modelId"] == "wocar_dqn/1"
    steps = gw.store.read_steps(run_id)
    assert [s["t"] for s in steps] == [0, 1, 2, 3, 4]
    s0 = steps[0]
    np.testing.assert_array_equal(np.asarray(s0["obs"], np.float32), obs(0).obs)
    assert s0["obsPerturbed"] == s0["obs"] and s0["linf"] == 0.0
    assert s0["action"] == actions[0] and s0["inferenceReplica"] == "i1"
    assert s0["perturbationReplica"] is None
    assert len(s0["q"]) == 5 and s0["poses"][0][0] == 0
    assert s0["rttMs"] == 3.0 and s0["gatewayMs"] >= 0
    assert gw.services.perturb_calls == []
    assert [c[2] for c in gw.services.act_calls] == [None] * 5


async def test_step_limit_finishes_the_run(gw):
    welcome = await gw.on_hello(hello(max_steps=2))
    rid = welcome.run_id
    await gw.on_obs(rid, obs(0))
    assert await gw.on_result(rid, result(0)) is None
    await gw.on_obs(rid, obs(1))
    summary = await gw.on_result(rid, result(1))
    assert isinstance(summary, SummaryOut) and summary.status == "finished" and summary.steps == 2


async def test_out_of_order_obs_ends_the_run(gw):
    rid = (await gw.on_hello(hello())).run_id
    out = await gw.on_obs(rid, obs(1))
    assert isinstance(out, SummaryOut) and (out.status, out.reason) == ("error", "PROTOCOL_VIOLATION")
    assert gw.store.read_run(rid)["status"] == "error"


async def test_labels_and_settings_are_stored(gw):
    rid, _, _ = await play(gw, 1, labels={"experiment": "e1"}, max_steps=1, pace_hz=1.0)
    run = gw.store.read_run(rid)
    assert run["labels"] == {"experiment": "e1"} and run["maxSteps"] == 1 and run["paceHz"] == 1.0


async def test_three_runs_at_once_stay_separate(gw):
    results = await asyncio.gather(*(play(gw, 4, seed=s) for s in (1, 2, 3)))
    ids = {r[0] for r in results}
    assert len(ids) == 3
    for rid, actions, _ in results:
        steps = gw.store.read_steps(rid)
        assert [s["action"] for s in steps] == actions
        assert [s["t"] for s in steps] == [0, 1, 2, 3]
