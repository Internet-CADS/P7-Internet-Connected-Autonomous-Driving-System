"""Issue 02: one clean run end to end, through real gRPC services and the gateway."""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from icads.vehicle.client import RunSettings, run_vehicle
from icads.vehicle.env import flatten, make_env
from tests.system import running_system

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def system(tmp_path_factory):
    with running_system(tmp_path_factory.mktemp("clean")) as gw:
        yield gw


def test_one_clean_run(system):
    out = run_vehicle(system.grpc_target, RunSettings("wocar_dqn/1", seed=7, max_steps=8,
                                                      labels={"note": "e2e"}))
    assert out.status == "finished" and out.steps == len(out.actions) > 0

    run = system.get(f"/v1/runs/{out.run_id}")
    assert run["status"] == "finished" and run["steps"] == out.steps
    assert run["maxSteps"] == 8 and run["labels"] == {"note": "e2e"}
    assert run["obsSpec"]["ranges"]["x"] == [-200.0, 200.0]
    assert len(run["road"]["lanes"]) == 4
    assert run["finalPoses"][0][0] == 0
    assert run["totalReward"] == pytest.approx(out.total_reward, rel=1e-5)

    log = system.get(f"/v1/runs/{out.run_id}/log")
    assert log[0]["type"] == "run" and log[-1]["type"] == "end"
    steps = [x for x in log if x["type"] == "step"]
    assert [s["action"] for s in steps] == out.actions

    # the stored clean observations are the ones highway-env gives for seed 7 and these actions
    env = make_env()
    obs, _ = env.reset(seed=7)
    for s, a in zip(steps, out.actions):
        np.testing.assert_array_equal(np.asarray(s["obs"], np.float32), flatten(obs))
        assert s["obsPerturbed"] == s["obs"] and s["cleanAction"] == s["action"]
        assert s["poses"][0][0] == 0 and len(s["poses"]) == 16
        assert s["inferenceReplica"] == "inference-1" and s["rttMs"] > 0
        obs, *_ = env.step(a)

    assert any(r["runId"] == out.run_id for r in system.get("/v1/runs")["runs"])
    assert len(system.get("/v1/models")["models"]) == 21


def test_pace_does_not_change_results(system):
    fast = run_vehicle(system.grpc_target, RunSettings("sa_dqn/2", seed=3, max_steps=3))
    start = time.monotonic()
    slow = run_vehicle(system.grpc_target, RunSettings("sa_dqn/2", seed=3, max_steps=3, pace_hz=4.0))
    assert time.monotonic() - start >= 2 * 0.25
    a = [s for s in system.get(f"/v1/runs/{fast.run_id}/log") if s["type"] == "step"]
    b = [s for s in system.get(f"/v1/runs/{slow.run_id}/log") if s["type"] == "step"]
    for x, y in zip(a, b, strict=True):
        assert (x["obs"], x["action"], x["q"], x["reward"]) == (y["obs"], y["action"], y["q"], y["reward"])
    assert system.get(f"/v1/runs/{slow.run_id}")["paceHz"] == 4.0


def test_three_vehicles_at_once(system):
    with ThreadPoolExecutor(3) as ex:
        outs = list(ex.map(lambda s: run_vehicle(system.grpc_target, RunSettings("vanilla_dqn/1", seed=s,
                                                                                  max_steps=6)), (1, 2, 3)))
    assert len({o.run_id for o in outs}) == 3
    for o in outs:
        steps = [s for s in system.get(f"/v1/runs/{o.run_id}/log") if s["type"] == "step"]
        assert [s["action"] for s in steps] == o.actions
        assert [s["t"] for s in steps] == list(range(o.steps))


def test_unknown_run_is_404(system):
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as e:
        system.get("/v1/runs/r-nope")
    assert e.value.code == 404


def test_importing_the_vehicle_client_never_loads_pytorch():
    code = ("import sys, icads.vehicle.client, icads.vehicle.env, icads.vehicle.demo\n"
            "bad = [m for m in sys.modules if m == 'torch' or m.startswith(('torch.', 'icads.policy', 'icads.attacks'))]\n"
            "assert not bad, bad\n")
    subprocess.run([sys.executable, "-c", code], check=True)


def test_vehicle_worker_plays_jobs_from_stdin(system):
    jobs = [{"model": "sa_dqn/4", "seed": 2, "maxSteps": 3, "labels": {"cell": "a"}},
            {"model": "wocar_dqn/5", "seed": 3, "attack": "pgd", "epsilon": 0.005, "maxSteps": 2}]
    proc = subprocess.run([sys.executable, "-m", "icads.cli", "vehicle", "--gateway", system.grpc_target, "--jobs"],
                          input="".join(json.dumps(j) + "\n" for j in jobs), capture_output=True, text=True,
                          timeout=120, check=True)
    outs = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [o["status"] for o in outs] == ["finished", "finished"]
    first = system.get(f"/v1/runs/{outs[0]['runId']}")
    second = system.get(f"/v1/runs/{outs[1]['runId']}")
    assert (first["modelId"], first["seed"], first["maxSteps"], first["labels"]) == ("sa_dqn/4", 2, 3, {"cell": "a"})
    assert (second["attack"], second["epsilon"]) == ("pgd", 0.005)
