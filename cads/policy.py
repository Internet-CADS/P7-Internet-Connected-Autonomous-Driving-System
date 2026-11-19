"""Load the existing CPU checkpoints and apply bounded observation perturbations."""

from functools import lru_cache
from pathlib import Path

import numpy as np
import torch
from torch import nn

from cads.protocol import FRAMES

ARTIFACTS = Path("entries/05_wocar_dqn_r3/artifacts/run_2")


@lru_cache(maxsize=21)
def load_model(root: Path, group: str, seed: int) -> nn.Module:
    checkpoint = root / ARTIFACTS / group / f"model_{seed}/frame_{FRAMES[group]}.pth"
    model = nn.Sequential(nn.Linear(50, 128), nn.ReLU(), nn.Linear(128, 128), nn.ReLU(), nn.Linear(128, 5))
    model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True))
    return model.eval().requires_grad_(False)


def perturb(
    observation: list[float],
    method: str,
    epsilon: float,
    seed: int,
    seq: int,
    model: nn.Module | None = None,
) -> tuple[list[float], float]:
    if epsilon == 0:
        return observation.copy(), 0.0

    clean = torch.tensor(observation, dtype=torch.float32).reshape(1, -1)
    rng = np.random.default_rng(np.random.SeedSequence([seed, seq]))
    noise = torch.tensor(rng.uniform(-epsilon, epsilon, clean.shape), dtype=clean.dtype)
    lower, upper = clean - epsilon, clean + epsilon
    point = (clean + noise).clamp(lower, upper)

    if method != "random_linf":
        if model is None:
            raise ValueError("A policy is required for gradient perturbations")
        with torch.no_grad():
            scores = model(clean)[0]
            best = scores.argmax()
            worst = scores.argmin()
        for _ in range(10):
            point.requires_grad_(True)
            scores = model(point)[0]
            if method == "pgd":
                objective = nn.functional.cross_entropy(scores, best)
            elif method == "minbest":
                other = torch.arange(scores.numel()) != best
                objective = scores[other].max() - scores[best]
            elif method == "worst_q":
                objective = scores[worst] - scores[best]
            else:
                raise ValueError(f"Unknown perturbation method: {method}")
            (gradient,) = torch.autograd.grad(objective, point)
            point = (point.detach() + (epsilon / 10) * gradient.sign()).clamp(lower, upper)

    values = point[0].tolist()
    magnitude = max(abs(after - before) for before, after in zip(observation, values, strict=True))
    if not 0 <= magnitude <= epsilon + 1e-6:
        raise ValueError("Perturbation exceeded its budget")
    return values, magnitude


def configure_cpu() -> None:
    torch.set_num_threads(1)


def perturb_step(root: Path, config: dict, observation: list[float], seq: int) -> tuple[list[float], float]:
    model = None
    if config["method"] != "random_linf" and config["epsilon"]:
        identity = config["model"]
        model = load_model(root, identity["group"], identity["seed"])
    return perturb(observation, config["method"], config["epsilon"], config["seed"], seq, model)
