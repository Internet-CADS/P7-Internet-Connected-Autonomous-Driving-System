"""The four state perturbations, ported from the models repository.

Source: https://github.com/Internet-CADS/models, commit 64a52648080ad5d4aa52bfa48ebdcf89356a5241,
`entries/05_wocar_dqn_r3/evaluate.py`, function `perturb()`.

Each attack is a pure function of (model, attack, epsilon, seed, step, observation).
One difference from the repository (docs/ARCHITECTURE.md, D11): the random start u comes from a
NumPy generator seeded with (seed, step) instead of one running `RandomState(1234)` stream, so the
answer never depends on which replica computes it or in which order.

As in the repository, only the epsilon-ball around the clean observation is enforced; there is
no clipping to [-1, 1] (D10).
"""

from __future__ import annotations

import numpy as np
import torch
from torch import nn

from icads.protocol.reference import OBS_SIZE

ATTACKS: tuple[str, ...] = ("random_linf", "pgd", "minbest", "worst_q")
PGD_STEPS = 10
MIN_STEP = 1e-6


def draws(seed: int, t: int) -> np.ndarray:
    """u: 50 values uniform in [-1, 1], from a generator seeded with (seed, t)."""
    rng = np.random.default_rng(np.random.SeedSequence([int(seed), int(t)]))
    return rng.uniform(-1.0, 1.0, OBS_SIZE).astype(np.float32)


def perturb(model: nn.Module | None, attack: str, epsilon: float, seed: int, t: int,
            obs: np.ndarray) -> np.ndarray:
    """The perturbed observation x' for the clean observation `obs` (float32, 50 values)."""
    return perturb_with_draws(model, attack, epsilon, obs, draws(seed, t))


def perturb_with_draws(model: nn.Module | None, attack: str, epsilon: float, obs: np.ndarray,
                       u: np.ndarray, niters: int = PGD_STEPS) -> np.ndarray:
    if attack not in ATTACKS:
        raise ValueError(f"unknown attack {attack!r}")
    x = torch.from_numpy(np.ascontiguousarray(obs, dtype=np.float32).reshape(1, OBS_SIZE))
    if epsilon <= 0.0:
        return x.numpy().reshape(-1).copy()
    noise = torch.from_numpy((epsilon * np.asarray(u, dtype=np.float32)).astype(np.float32).reshape(1, OBS_SIZE))
    if attack == "random_linf":
        return (x + noise).numpy().reshape(-1)
    if model is None:
        raise ValueError(f"{attack} needs a model")

    with torch.no_grad():
        q = model(x)
        best, worst = q.argmax(dim=1), q.argmin(dim=1)
    step = max(epsilon / float(niters), MIN_STEP)
    adv = (x + noise).clamp(x - epsilon, x + epsilon).detach()
    for _ in range(niters):
        adv.requires_grad_(True)
        q_adv = model(adv)
        if attack == "pgd":
            loss = nn.CrossEntropyLoss()(q_adv, best)
        elif attack == "minbest":
            mask = torch.zeros_like(q_adv).scatter_(1, best.unsqueeze(1), 1e10)
            loss = ((q_adv - mask).max(dim=1)[0] - q_adv.gather(1, best.unsqueeze(1)).squeeze(1)).sum()
        else:  # worst_q
            loss = (q_adv.gather(1, worst.unsqueeze(1)).squeeze(1)
                    - q_adv.gather(1, best.unsqueeze(1)).squeeze(1)).sum()
        (grad,) = torch.autograd.grad(loss, adv)
        adv = (adv.detach() + step * grad.sign()).clamp(x - epsilon, x + epsilon)
    return adv.detach().numpy().reshape(-1)


def within_bound(clean: np.ndarray, perturbed: np.ndarray, epsilon: float, tol: float = 1e-6) -> bool:
    return bool(np.all(np.isfinite(perturbed))) and float(np.max(np.abs(perturbed - clean))) <= epsilon + tol
