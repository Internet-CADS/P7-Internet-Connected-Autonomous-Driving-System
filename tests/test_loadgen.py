"""Issue 10: replay vehicles, simulated viewers, the measurements and /load."""

import json

import pytest

from icads.loadgen.profile import Phase, Profile
from icads.loadgen.runner import percentiles, run_profile
from icads.vehicle.client import RunSettings, run_vehicle
from tests.system import running_system

pytestmark = pytest.mark.slow


def test_percentiles():
    p = percentiles(list(range(1, 101)))
    assert (p["p50"], p["p95"], p["p99"], p["n"]) == (pytest.approx(50.5), pytest.approx(95.05),
                                                       pytest.approx(99.01), 100)
    assert percentiles([])["n"] == 0


def test_profile_phases(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"name": "throughput", "recording": "rec.jsonl", "attack": "pgd", "epsilon": 0.01,
                             "paceHz": 0, "viewersPerRun": 0, "durationS": 5, "runs": [1, 2, 4]}))
    prof = Profile.load(p)
    assert [ph.runs for ph in prof.phases()] == [1, 2, 4]
    assert all(isinstance(ph, Phase) and ph.attack == "pgd" for ph in prof.phases())
    assert prof.recording_path == tmp_path / "rec.jsonl"


@pytest.fixture(scope="module")
def system(tmp_path_factory):
    with running_system(tmp_path_factory.mktemp("load"), max_runs=4) as gw:
        yield gw


@pytest.fixture(scope="module")
def recording(system, tmp_path_factory):
    out = run_vehicle(system.grpc_target, RunSettings("vanilla_dqn/1", seed=7, max_steps=12,
                                                      labels={"synthetic": "true"}))
    path = tmp_path_factory.mktemp("rec") / "rec.jsonl"
    import urllib.request
    with urllib.request.urlopen(f"{system.http}/v1/runs/{out.run_id}/log") as r:
        path.write_bytes(r.read())
    return path


def test_replay_vehicles_and_viewers_are_measured(system, recording, tmp_path):
    prof = tmp_path / "p.json"
    prof.write_text(json.dumps({"name": "small", "recording": str(recording), "attack": "pgd", "epsilon": 0.005,
                                "paceHz": 0, "viewersPerRun": 2, "durationS": 1.5, "runs": [2],
                                "loadSampleS": 0.2}))
    before = {r["runId"] for r in system.get("/v1/runs?synthetic=all&limit=1000")["runs"]}
    assert run_profile(str(prof), system.grpc_target, system.http, str(tmp_path / "out")) == 0
    files = list((tmp_path / "out").glob("small-*.json"))
    assert len(files) == 1
    result = json.loads(files[0].read_text())
    phase = result["phases"][0]
    assert phase["runs"] == 2 and phase["finishedRuns"] >= 2 and phase["failedRuns"] == 0
    assert phase["refused"] == 0 and phase["stepsPerSecond"] > 0
    assert phase["rttMs"]["n"] == phase["steps"] > 0 and phase["rttMs"]["p50"] > 0
    assert phase["viewerDelayMs"]["n"] == 2 * phase["steps"]
    assert phase["load"] and {"activeRuns", "maxRuns", "viewers", "eventLoopLagMs", "writerQueue",
                              "inFlight"} <= set(phase["load"][0])
    env = result["environment"]
    for key in ("hardware", "cores", "cpuLimit", "replicas", "runtime", "loadGeneratorSharesMachine", "imageId"):
        assert key in env
    assert env["replicas"] == {"inference": 1, "perturbation": 1}  # from the gateway, not typed by hand
    assert phase["replicasSeen"] == {"inference": ["inference-1"], "perturbation": ["perturbation-1"]}
    assert env["cpuLimit"]["services"] == "none (plain processes)"
    # replayed runs are labelled synthetic, hidden from the page's list
    new = [r for r in system.get("/v1/runs?synthetic=all&limit=1000")["runs"] if r["runId"] not in before]
    assert new and all(r["labels"].get("synthetic") == "true" for r in new)
    assert not any(r["runId"] in {n["runId"] for n in new} for r in system.get("/v1/runs?limit=1000")["runs"])


def test_overload_counts_refusals(system, recording, tmp_path):
    prof = tmp_path / "o.json"
    prof.write_text(json.dumps({"name": "overload", "recording": str(recording), "attack": "none", "epsilon": 0,
                                "paceHz": 4, "viewersPerRun": 0, "durationS": 1.0, "runs": ["2x"]}))
    run_profile(str(prof), system.grpc_target, system.http, str(tmp_path / "out"))
    result = json.loads(next((tmp_path / "out").glob("overload-*.json")).read_text())
    phase = result["phases"][0]
    assert phase["runs"] == 8  # twice the limit of 4
    assert phase["refused"] >= 4 and phase["failedRuns"] == 0


def test_load_endpoint(system):
    load = system.get("/load")
    assert load["maxRuns"] == 4 and set(load["inFlight"]) == {"perturbation", "inference"}
    assert load["writerQueue"] >= 0 and load["eventLoopLagMs"] >= 0


def test_a_missing_recording_is_made_with_a_vehicle_process(system, tmp_path):
    from icads.loadgen.profile import Profile
    from icads.loadgen.runner import Recording, record

    p = tmp_path / "p.json"
    p.write_text(json.dumps({"name": "r", "recording": "made.jsonl", "recordModel": "wocar_dqn/1", "recordSeed": 7}))
    prof = Profile.load(p)
    record(prof, system.grpc_target, system.http)
    rec = Recording.load(prof.recording_path)
    assert rec.run["modelId"] == "wocar_dqn/1" and rec.run["labels"]["synthetic"] == "true" and rec.steps
