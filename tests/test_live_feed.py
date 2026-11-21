"""Issue 05 (and the data side of 06): REST run list and server-sent events."""

import asyncio
import threading
import time

import pytest

from icads.gateway.core import Event, Viewer
from icads.vehicle.client import RunSettings, run_vehicle
from tests.sse import sse
from tests.system import running_system

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def system(tmp_path_factory):
    with running_system(tmp_path_factory.mktemp("live")) as gw:
        yield gw


def collect(url, last_event_id=None, sink=None):
    out = sink if sink is not None else []
    for ev in sse(url, last_event_id):
        out.append(ev)
    return out


def test_finished_run_stream_is_meta_steps_end(system):
    out = run_vehicle(system.grpc_target, RunSettings("sa_dqn/3", seed=4, max_steps=5, attack="pgd", epsilon=0.005))
    events = collect(f"{system.http}/v1/runs/{out.run_id}/events")
    names = [e.name for e in events]
    assert names[0] == "meta" and names[-1] == "end" and names[1:-1] == ["step"] * out.steps
    meta = events[0].data
    assert meta["actionNames"][3] == "FASTER" and meta["obsSpec"]["ranges"]["y"] == [-16, 16]
    assert meta["settings"]["attack"] == "pgd" and meta["settings"]["epsilon"] == 0.005
    step = events[1].data
    for key in ("t", "poses", "obs", "obsPerturbed", "linf", "action", "q", "cleanAction", "cleanQ", "reward",
                "terminated", "truncated", "crashed"):
        assert key in step
    assert [e.id for e in events[1:-1]] == list(range(out.steps))
    assert events[-1].data["status"] == "finished"

    resumed = collect(f"{system.http}/v1/runs/{out.run_id}/events", last_event_id=2)
    assert [e.id for e in resumed if e.name == "step"] == list(range(3, out.steps))


def test_live_viewers_get_every_step_after_it_is_stored(system):
    result = {}
    vehicle = threading.Thread(target=lambda: result.update(out=run_vehicle(
        system.grpc_target, RunSettings("wocar_dqn/2", seed=5, max_steps=12, pace_hz=8.0))))
    vehicle.start()
    run = None
    while run is None:
        run = next((r for r in system.get("/v1/runs?status=running")["runs"] if r["modelId"] == "wocar_dqn/2"), None)
        time.sleep(0.02)
    url = f"{system.http}/v1/runs/{run['runId']}/events"
    seen_in_store = []

    def watch(sink, check=False):
        for ev in sse(url):
            sink.append(ev)
            if check and ev.name == "step":
                # a step is only shown after it is committed
                stored = [s["t"] for s in system.get(f"/v1/runs/{run['runId']}/log") if s["type"] == "step"]
                seen_in_store.append(ev.id in stored)

    a, b = [], []
    ta = threading.Thread(target=watch, args=(a, True))
    ta.start()
    time.sleep(0.6)  # the second viewer joins later and catches up
    tb = threading.Thread(target=watch, args=(b,))
    tb.start()
    vehicle.join(30)
    ta.join(30)
    tb.join(30)
    steps = result["out"].steps
    for events in (a, b):
        assert events[0].name == "meta" and events[-1].name == "end"
        assert [e.id for e in events if e.name == "step"] == list(range(steps))
    assert [e.data for e in a if e.name == "step"] == [e.data for e in b if e.name == "step"]
    assert seen_in_store and all(seen_in_store)


def test_run_list_stream_announces_start_and_end(system):
    got = []

    def listen():
        for ev in sse(f"{system.http}/v1/events"):
            got.append(ev)
            if ev.name == "run" and ev.data["status"] == "finished" and ev.data["modelId"] == "sa_dqn/6":
                return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(0.3)
    out = run_vehicle(system.grpc_target, RunSettings("sa_dqn/6", seed=1, max_steps=2))
    t.join(10)
    mine = [e.data for e in got if e.data["runId"] == out.run_id]
    assert [d["status"] for d in mine] == ["running", "finished"]


def test_synthetic_runs_are_hidden_from_the_list(system):
    out = run_vehicle(system.grpc_target, RunSettings("sa_dqn/7", max_steps=1, labels={"synthetic": "true"}))
    ids = [r["runId"] for r in system.get("/v1/runs")["runs"]]
    assert out.run_id not in ids
    assert out.run_id in [r["runId"] for r in system.get("/v1/runs?synthetic=true")["runs"]]


def test_list_is_newest_first(system):
    runs = system.get("/v1/runs")["runs"]
    created = [r["createdAt"] for r in runs]
    assert created == sorted(created, reverse=True)


async def test_a_viewer_that_falls_behind_is_dropped():
    v = Viewer(maxsize=3)
    ev = Event("step", b"{}", id=0)
    assert v.offer(ev) and v.offer(ev) and v.offer(ev)
    assert not v.offer(ev)  # full: dropped, never blocks the run
    assert v.dropped
    assert await asyncio.wait_for(v.queue.get(), 1) is None  # the reader is told to disconnect


def test_page_is_served(system):
    import urllib.request
    with urllib.request.urlopen(system.http + "/", timeout=5) as r:
        html = r.read().decode()
    assert "<canvas" in html and "app.js" in html
    with urllib.request.urlopen(system.http + "/app.js", timeout=5) as r:
        assert b"EventSource" in r.read()


def test_page_files_are_revalidated_so_a_rebuild_is_picked_up(system):
    # Without this, browsers may keep an old app.js next to a new index.html after a rebuild.
    import urllib.request
    for path in ("/", "/app.js", "/replay.js", "/style.css"):
        with urllib.request.urlopen(system.http + path, timeout=5) as r:
            assert r.headers.get("Cache-Control") == "no-cache", path


def test_the_run_list_shows_live_step_counts(system):
    vehicle = threading.Thread(target=lambda: run_vehicle(
        system.grpc_target, RunSettings("vanilla_dqn/4", seed=1, max_steps=20, pace_hz=10.0)))
    vehicle.start()
    counts = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        live = [r for r in system.get("/v1/runs?status=running")["runs"] if r["modelId"] == "vanilla_dqn/4"]
        if live:
            counts.append(live[0]["steps"])
            one = system.get(f"/v1/runs/{live[0]['runId']}")
            assert one["steps"] >= live[0]["steps"]
        if counts and counts[-1] >= 3:
            break
        time.sleep(0.1)
    vehicle.join(30)
    assert counts and counts[-1] >= 3
