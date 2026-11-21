"""Record reference values with the models repository's own code (run once, by hand).

    uv run python tests/fixtures/make_reference.py <path to a clone of Internet-CADS/models>

Writes `tests/fixtures/reference.json`:

- `observations`: a few fixed 50-value observations (real highway-v0 observations and uniform noise)
- `q`: the Q-values the repository's loader (`models.model_setup` + `features.load_state_dict`)
  gives for them, for every one of the 21 checkpoints
- `attacks`: the repository's `perturb()` (entries/05_wocar_dqn_r3/evaluate.py) on some of them,
  for every attack, with the random draws u given explicitly (they are stored too)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import gymnasium as gym
import highway_env  # noqa: F401
import numpy as np
import torch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from icads.protocol.reference import ENV_CONFIG, GROUPS, MODEL_IDS  # noqa: E402

torch.set_num_threads(1)


class GivenDraws:
    """Stands in for numpy's RandomState in `perturb()`: uniform(-eps, eps) returns eps * u."""

    def __init__(self, u: np.ndarray):
        self.u = u

    def uniform(self, low, high, size):
        assert low == -high
        return (high * self.u).reshape(size)


def main(models_repo: Path) -> None:
    entry = models_repo / "entries" / "05_wocar_dqn_r3"
    sys.path.insert(0, str(entry))
    import evaluate  # the repository's script; it changes cwd to its folder  # noqa: E402
    from models import model_setup  # noqa: E402

    evaluate._CTX.update(np=np, torch=torch)
    commit = subprocess.check_output(["git", "-C", str(models_repo), "rev-parse", "HEAD"], text=True).strip()

    env = gym.make("highway-v0", config=ENV_CONFIG)
    observations = []
    for seed in (0, 1, 2):
        obs, _ = env.reset(seed=seed)
        observations.append(np.asarray(obs, dtype=np.float32).reshape(-1))
        obs, *_ = env.step(1)
        observations.append(np.asarray(obs, dtype=np.float32).reshape(-1))
    rng = np.random.default_rng(20261121)
    observations += [rng.uniform(-1, 1, 50).astype(np.float32) for _ in range(2)]

    class FlatEnv:  # what model_setup reads from the repository's adapter
        observation_space = type("S", (), {"shape": (50,)})()
        action_space = type("A", (), {"n": 5})()

    x = torch.from_numpy(np.stack(observations))
    q = {}
    nets = {}
    for model_id in MODEL_IDS:
        group, seed = model_id.split("/")
        path = entry / "artifacts" / "run_2" / group / f"model_{seed}" / f"frame_{GROUPS[group]}.pth"
        net = model_setup("highway-v0", FlatEnv(), robust_model=False, logger=None, use_cuda=False,
                          dueling=True, model_width=1)
        net.features.load_state_dict(torch.load(path, map_location="cpu"))
        net.eval()
        nets[model_id] = net
        with torch.no_grad():
            q[model_id] = net(x).numpy().tolist()

    attacks = []
    draw_rng = np.random.default_rng(7)
    for model_id in ("vanilla_dqn/1", "wocar_dqn/3"):
        for i in (0, 3, 6):
            u = draw_rng.uniform(-1, 1, 50).astype(np.float32)
            for attack in ("random_linf", "pgd", "minbest", "worst_q"):
                for eps in (0.005, 0.01):
                    xi = x[i : i + 1]
                    out = evaluate.perturb(nets[model_id], xi, attack, eps, GivenDraws(u))
                    attacks.append(dict(model=model_id, obs_index=i, attack=attack, epsilon=eps,
                                        u=u.tolist(), perturbed=out.numpy().reshape(-1).tolist()))

    data = dict(source=dict(repository="https://github.com/Internet-CADS/models", commit=commit,
                            loader="entries/05_wocar_dqn_r3/models.py model_setup",
                            attacks="entries/05_wocar_dqn_r3/evaluate.py perturb"),
                observations=[o.tolist() for o in observations], q=q, attacks=attacks)
    (HERE / "reference.json").write_text(json.dumps(data))
    print(f"wrote {len(observations)} observations, {len(q)} models, {len(attacks)} attack cases")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
