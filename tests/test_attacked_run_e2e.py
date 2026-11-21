"""Issue 04: attacked runs through the whole system."""

import pytest

from icads.vehicle.client import RunSettings, run_vehicle
from tests.system import running_system

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def system(tmp_path_factory):
    with running_system(tmp_path_factory.mktemp("attacked")) as gw:
        yield gw


def steps_of(system, run_id):
    return [s for s in system.get(f"/v1/runs/{run_id}/log") if s["type"] == "step"]


def test_same_command_twice_gives_identical_runs(system):
    settings = RunSettings("wocar_dqn/1", seed=7, attack="pgd", epsilon=0.01, max_steps=10)
    a, b = run_vehicle(system.grpc_target, settings), run_vehicle(system.grpc_target, settings)
    assert a.status == b.status == "finished"
    sa, sb = steps_of(system, a.run_id), steps_of(system, b.run_id)
    assert len(sa) == len(sb) > 0
    for x, y in zip(sa, sb):
        for k in ("obs", "obsPerturbed", "action", "reward", "cleanAction", "q"):
            assert x[k] == y[k], k
        assert 0 < x["linf"] <= 0.01 + 1e-6
        assert x["perturbationReplica"] == "perturbation-1"


def test_pgd_changes_some_actions_of_vanilla_dqn(system):
    flips = 0
    for seed in range(3):
        out = run_vehicle(system.grpc_target, RunSettings("vanilla_dqn/1", seed=seed, attack="pgd", epsilon=0.01))
        flips += sum(s["action"] != s["cleanAction"] for s in steps_of(system, out.run_id))
    assert flips > 0
