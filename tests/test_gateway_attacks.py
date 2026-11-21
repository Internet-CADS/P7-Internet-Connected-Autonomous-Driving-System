"""Issue 04 on the run logic: when Perturb is called, what Act receives, what is stored, the bound check."""

import asyncio

import numpy as np
import pytest

from icads.gateway.core import Gateway
from icads.gateway.model import ActionOut, SummaryOut
from icads.gateway.store import Store
from tests.gateway_helpers import FakeServices, hello, obs, result


@pytest.fixture
async def make_gw(tmp_path):
    stores = []

    def make(services):
        store = Store(tmp_path / f"runs{len(stores)}.sqlite")
        store.start(asyncio.get_running_loop())
        stores.append(store)
        return Gateway(store, services)

    yield make
    for s in stores:
        s.close()


@pytest.mark.parametrize("attack,eps", [("none", 0.01), ("pgd", 0.0)])
async def test_no_perturbation_for_attack_none_or_zero_epsilon(make_gw, attack, eps):
    svc = FakeServices()
    gw = make_gw(svc)
    rid = (await gw.on_hello(hello(attack=attack, epsilon=eps))).run_id
    await gw.on_obs(rid, obs(0))
    await gw.on_result(rid, result(0, done=True))
    assert svc.perturb_calls == []
    assert svc.act_calls[0][2] is None


async def test_attacked_step_uses_the_perturbed_action_and_stores_both(make_gw):
    svc = FakeServices()
    gw = make_gw(svc)
    rid = (await gw.on_hello(hello(attack="pgd", epsilon=0.01, seed=9))).run_id
    o = obs(0)
    out = await gw.on_obs(rid, o)
    await gw.on_result(rid, result(0, done=True))
    assert svc.perturb_calls == [("wocar_dqn/1", "pgd", pytest.approx(0.01), 9, 0)]
    _, sent_obs, sent_clean = svc.act_calls[0]
    np.testing.assert_array_equal(sent_clean, o.obs)
    np.testing.assert_allclose(sent_obs, o.obs + np.float32(0.01))
    assert isinstance(out, ActionOut) and out.action == int(np.argmax(sent_obs[:5]))

    step = gw.store.read_steps(rid)[0]
    np.testing.assert_allclose(step["obsPerturbed"], sent_obs)
    assert step["cleanAction"] == int(np.argmax(o.obs[:5]))
    assert step["linf"] == pytest.approx(0.01, abs=1e-6)
    assert step["perturbationReplica"] == "p1" and step["perturbHandleMs"] == 1.0 and step["perturbMs"] == 1.5


async def test_a_perturbation_over_the_bound_ends_the_run(make_gw):
    gw = make_gw(FakeServices(shift=0.02))
    rid = (await gw.on_hello(hello(attack="random_linf", epsilon=0.01))).run_id
    out = await gw.on_obs(rid, obs(0))
    assert isinstance(out, SummaryOut) and (out.status, out.reason) == ("error", "SERVICE_ERROR")
    assert gw.store.read_run(rid)["reason"] == "SERVICE_ERROR"
