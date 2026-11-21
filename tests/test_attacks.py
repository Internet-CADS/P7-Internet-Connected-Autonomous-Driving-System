import subprocess
import sys

import numpy as np
import pytest

from icads.attacks import ATTACKS, draws, perturb, perturb_with_draws
from icads.policy import load_model

NONZERO_EPS = (0.0025, 0.005, 0.0075, 0.01)


@pytest.fixture(scope="module")
def nets():
    return {m: load_model(m) for m in ("vanilla_dqn/1", "wocar_dqn/3", "sa_dqn/5")}


def test_port_matches_the_repository_given_the_same_draws(reference, reference_obs, nets):
    assert len(reference["attacks"]) >= 4 * 2
    for case in reference["attacks"]:
        out = perturb_with_draws(nets[case["model"]], case["attack"], case["epsilon"],
                                 reference_obs[case["obs_index"]], np.asarray(case["u"], np.float32))
        np.testing.assert_allclose(out, np.asarray(case["perturbed"], np.float32), rtol=0, atol=1e-7,
                                   err_msg=f"{case['attack']} eps={case['epsilon']}")


def test_draws_depend_only_on_seed_and_step():
    u = draws(7, 3)
    assert u.shape == (50,) and u.dtype == np.float32
    assert np.all(u >= -1) and np.all(u <= 1)
    np.testing.assert_array_equal(u, draws(7, 3))
    assert not np.array_equal(u, draws(7, 4))
    assert not np.array_equal(u, draws(8, 3))


def test_random_linf_is_x_plus_eps_u(reference_obs):
    x = reference_obs[0]
    out = perturb(None, "random_linf", 0.01, seed=7, t=3, obs=x)
    np.testing.assert_array_equal(out, x + (np.float32(0.01) * draws(7, 3)).astype(np.float32))


@pytest.mark.parametrize("attack", ATTACKS)
@pytest.mark.parametrize("eps", NONZERO_EPS)
def test_bound_and_repeatability(attack, eps, reference_obs, nets):
    net = nets["vanilla_dqn/1"]
    for i, x in enumerate(reference_obs):
        out = perturb(net, attack, eps, seed=11, t=i, obs=x)
        assert out.dtype == np.float32 and out.shape == (50,)
        assert np.all(np.isfinite(out))
        assert np.max(np.abs(out - x)) <= eps + 1e-6
        np.testing.assert_array_equal(out, perturb(net, attack, eps, seed=11, t=i, obs=x))


def test_no_clipping_to_the_observation_range(nets):
    x = np.ones(50, np.float32)
    out = perturb(nets["sa_dqn/5"], "random_linf", 0.01, seed=1, t=0, obs=x)
    assert out.max() > 1.0


def test_unknown_attack(nets):
    with pytest.raises(ValueError):
        perturb(nets["sa_dqn/5"], "fgsm", 0.01, 0, 0, np.zeros(50, np.float32))


def test_same_answer_in_a_second_process(reference_obs, nets):
    x = reference_obs[2]
    here = perturb(nets["wocar_dqn/3"], "minbest", 0.0075, seed=5, t=9, obs=x)
    code = (
        "import numpy as np, sys, json\n"
        "from icads.attacks import perturb\nfrom icads.policy import load_model, set_single_thread\n"
        "set_single_thread()\n"
        f"x=np.asarray({x.tolist()!r}, np.float32)\n"
        "out=perturb(load_model('wocar_dqn/3'),'minbest',0.0075,seed=5,t=9,obs=x)\n"
        "sys.stdout.write(out.tobytes().hex())\n"
    )
    there = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    np.testing.assert_array_equal(here, np.frombuffer(bytes.fromhex(there), np.float32))
