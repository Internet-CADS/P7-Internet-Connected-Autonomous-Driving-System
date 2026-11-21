"""Issue 08 on the run logic: refusals, bad input, service failures, restart."""

import asyncio
import json
import math

import numpy as np
import pytest

from icads.gateway.core import Gateway
from icads.gateway.model import ActionOut, ObsData, ServiceFailed, ServiceUnavailable, SummaryOut, WelcomeOut
from icads.gateway.store import Store
from icads.protocol.reference import ENV_CONFIG
from tests.gateway_helpers import FakeServices, hello, obs, result


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "runs.sqlite")
    s.start(asyncio.get_running_loop())
    yield s
    s.close()


@pytest.fixture
def gw(store):
    return Gateway(store, FakeServices(), max_runs=2)


other_env = dict(ENV_CONFIG, vehicles_count=20)


@pytest.mark.parametrize("over,reason", [
    (dict(protocol_version=2), "PROTOCOL_VERSION"),
    (dict(scenario="merge-v0"), "SPEC_MISMATCH"),
    (dict(env_config_json=json.dumps(other_env)), "SPEC_MISMATCH"),
    (dict(obs_vehicles=5), "SPEC_MISMATCH"),
    (dict(obs_absolute=True), "SPEC_MISMATCH"),
    (dict(obs_features=["presence", "x", "y"]), "SPEC_MISMATCH"),
    (dict(obs_ranges={}), "SPEC_MISMATCH"),
    (dict(action_names=["IDLE"] * 5), "SPEC_MISMATCH"),
    (dict(model_id="wocar_dqn/9"), "UNKNOWN_MODEL"),
    (dict(attack="fgsm"), "UNKNOWN_ATTACK"),
    (dict(epsilon=-0.01), "BAD_EPSILON"),
    (dict(epsilon=math.nan), "BAD_EPSILON"),
])
async def test_bad_hello_is_refused_and_not_stored(gw, over, reason):
    out = await gw.on_hello(hello(**over))
    assert isinstance(out, SummaryOut) and (out.status, out.reason) == ("refused", reason)
    assert gw.store.read_runs(synthetic="all") == []
    assert gw.sessions == {}


async def test_overloaded_refuses_at_once_and_leaves_running_runs_alone(gw):
    a = await gw.on_hello(hello())
    b = await gw.on_hello(hello())
    c = await gw.on_hello(hello())
    assert isinstance(a, WelcomeOut) and isinstance(b, WelcomeOut)
    assert isinstance(c, SummaryOut) and (c.status, c.reason) == ("refused", "OVERLOADED")
    assert isinstance(await gw.on_obs(a.run_id, obs(0)), ActionOut)
    assert len(gw.store.read_runs()) == 2


def bad(t, values):
    return ObsData(t=t, obs=np.asarray(values, np.float32), poses=np.zeros((1, 5), np.float32))


@pytest.mark.parametrize("make,reason", [
    (lambda: bad(0, np.zeros(49)), "BAD_OBS_SHAPE"),
    (lambda: bad(0, [np.nan] + [0.0] * 49), "NON_FINITE_OBS"),
    (lambda: bad(0, [np.inf] + [0.0] * 49), "NON_FINITE_OBS"),
    (lambda: bad(0, [1.01] + [0.0] * 49), "OBS_OUT_OF_RANGE"),
    (lambda: bad(3, np.zeros(50)), "PROTOCOL_VIOLATION"),
])
async def test_bad_obs_ends_only_that_run(gw, make, reason):
    victim = (await gw.on_hello(hello())).run_id
    other = (await gw.on_hello(hello())).run_id
    out = await gw.on_obs(victim, make())
    assert isinstance(out, SummaryOut) and (out.status, out.reason) == ("error", reason)
    assert gw.store.read_run(victim)["reason"] == reason
    assert isinstance(await gw.on_obs(other, obs(0)), ActionOut)
    assert gw.store.read_run(other)["status"] == "running"


async def test_values_at_the_edge_of_the_range_are_accepted(gw):
    rid = (await gw.on_hello(hello())).run_id
    assert isinstance(await gw.on_obs(rid, bad(0, [1.0, -1.0] + [0.0] * 48)), ActionOut)


async def test_result_before_obs_is_a_protocol_violation(gw):
    rid = (await gw.on_hello(hello())).run_id
    out = await gw.on_result(rid, result(0))
    assert isinstance(out, SummaryOut) and out.reason == "PROTOCOL_VIOLATION"


class Failing(FakeServices):
    def __init__(self, where, exc):
        super().__init__()
        self.where, self.exc = where, exc

    async def perturb(self, *a, **k):
        if self.where == "perturb":
            raise self.exc
        return await super().perturb(*a, **k)

    async def act(self, *a, **k):
        if self.where == "act":
            raise self.exc
        return await super().act(*a, **k)


@pytest.mark.parametrize("where,exc,reason", [
    ("perturb", ServiceUnavailable("perturbation", "down"), "PERTURBATION_UNAVAILABLE"),
    ("act", ServiceUnavailable("inference", "down"), "INFERENCE_UNAVAILABLE"),
    ("perturb", ServiceFailed("self-check"), "SERVICE_ERROR"),
    ("act", ServiceFailed("boom"), "SERVICE_ERROR"),
])
async def test_service_failures_end_the_run_without_an_action(store, where, exc, reason):
    gw = Gateway(store, Failing(where, exc))
    rid = (await gw.on_hello(hello(attack="pgd", epsilon=0.01))).run_id
    out = await gw.on_obs(rid, obs(0))
    assert isinstance(out, SummaryOut) and (out.status, out.reason) == ("error", reason)
    assert gw.store.read_steps(rid) == []


async def test_disconnect_and_timeout(gw):
    a = (await gw.on_hello(hello())).run_id
    b = (await gw.on_hello(hello())).run_id
    await gw.on_disconnect(a)
    out = await gw.on_timeout(b)
    assert gw.store.read_run(a)["reason"] == "VEHICLE_DISCONNECTED"
    assert isinstance(out, SummaryOut) and gw.store.read_run(b)["reason"] == "VEHICLE_TIMEOUT"


async def test_abort_pushes_a_summary(gw):
    rid = (await gw.on_hello(hello())).run_id
    summary = await gw.abort(rid)
    assert summary is not None and summary.status == "aborted"
    assert gw.sessions == {}
    pushed = gw.sessions.get(rid)
    assert pushed is None
    assert gw.store.read_run(rid)["status"] == "aborted"
    assert await gw.abort(rid) is None


async def test_runs_left_running_become_gateway_restart(tmp_path):
    s1 = Store(tmp_path / "r.sqlite")
    s1.start(asyncio.get_running_loop())
    gw = Gateway(s1, FakeServices())
    rid = (await gw.on_hello(hello())).run_id
    s1.close()  # the gateway "dies" with the run still running
    s2 = Store(tmp_path / "r.sqlite")
    assert s2.mark_restart() == 1
    run = s2.read_run(rid)
    assert run is not None
    assert (run["status"], run["reason"]) == ("error", "GATEWAY_RESTART")


class SlowFinish:
    """Wraps a store so the test decides when the run's final state is committed."""

    def __init__(self, store):
        self.store = store
        self.release = asyncio.Event()

    def __getattr__(self, name):
        return getattr(self.store, name)

    def submit_finish(self, *args, **kwargs):
        inner = self.store.submit_finish(*args, **kwargs)
        release = self.release

        async def wait():
            await release.wait()
            return await inner

        return asyncio.ensure_future(wait())


async def test_a_viewer_joining_while_a_run_ends_gets_the_real_end(store):
    slow = SlowFinish(store)
    gw = Gateway(slow, FakeServices())  # pyright: ignore[reportArgumentType]
    rid = (await gw.on_hello(hello(max_steps=1))).run_id
    await gw.on_obs(rid, obs(0))
    ending = asyncio.ensure_future(gw.on_result(rid, result(0)))
    await asyncio.sleep(0.05)  # the run has ended for the vehicle, but is not yet committed
    session = gw.active_feed(rid)
    assert session is not None, "a viewer must still follow the live run until its end is stored"
    from icads.gateway.core import Viewer
    v = Viewer()
    session.feed.viewers.add(v)
    slow.release.set()
    summary = await ending
    assert summary is not None and summary.status == "finished"
    events = []
    while not v.queue.empty():
        events.append(v.queue.get_nowait())
    end = [e for e in events if e is not None and e.name == "end"]
    assert len(end) == 1 and json.loads(end[0].data)["status"] == "finished"
    assert gw.active_feed(rid) is None


class FailingCreate:
    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)

    def submit_create(self, row):
        fut = asyncio.get_running_loop().create_future()
        fut.set_exception(RuntimeError("disk full"))
        return fut


async def test_a_run_that_cannot_be_stored_frees_its_slot(store):
    gw = Gateway(FailingCreate(store), FakeServices(), max_runs=1)  # pyright: ignore[reportArgumentType]
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await gw.on_hello(hello())
    assert gw.sessions == {}


async def test_a_stream_that_does_not_start_with_hello_is_refused(gw):
    out = await gw.on_unexpected(None, "obs")
    assert isinstance(out, SummaryOut) and (out.status, out.reason) == ("refused", "PROTOCOL_VIOLATION")
    assert gw.store.read_runs(synthetic="all") == []


async def test_a_second_hello_ends_the_run(gw):
    rid = (await gw.on_hello(hello())).run_id
    out = await gw.on_unexpected(rid, "hello")
    assert isinstance(out, SummaryOut) and (out.status, out.reason) == ("error", "PROTOCOL_VIOLATION")
    assert gw.store.read_run(rid)["reason"] == "PROTOCOL_VIOLATION"
    assert gw.ended(rid) == out


async def test_shutting_down_ends_active_runs_as_gateway_restart(gw):
    a = (await gw.on_hello(hello())).run_id
    b = (await gw.on_hello(hello())).run_id
    await gw.shutdown()
    for rid in (a, b):
        run = gw.store.read_run(rid)
        assert (run["status"], run["reason"]) == ("error", "GATEWAY_RESTART")
        pushed = await asyncio.wait_for(gw.next_push(rid), 1)  # the vehicle is told
        assert pushed.reason == "GATEWAY_RESTART"
    await gw.on_disconnect(a)  # the stream closing afterwards changes nothing
    assert gw.store.read_run(a)["reason"] == "GATEWAY_RESTART"
