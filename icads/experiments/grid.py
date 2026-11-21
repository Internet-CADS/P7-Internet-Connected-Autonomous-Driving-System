"""The experiment grid: model groups x checkpoint seeds x attacks x epsilon x episodes."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from icads.protocol.reference import ATTACKS, DEFAULT_MAX_STEPS, GROUPS, SEEDS


@dataclass(frozen=True)
class Grid:
    experiment: str
    groups: list[str]
    seeds: list[int]
    attacks: list[str]
    epsilons: list[float]
    episodes: int
    workers: int = field(default_factory=lambda: os.cpu_count() or 1)
    max_steps: int = DEFAULT_MAX_STEPS

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Grid":
        g = cls(experiment=str(d["experiment"]), groups=list(d["groups"]), seeds=[int(s) for s in d["seeds"]],
                attacks=list(d["attacks"]), epsilons=[float(e) for e in d["epsilons"]],
                episodes=int(d["episodes"]), workers=int(d.get("workers") or os.cpu_count() or 1),
                max_steps=int(d.get("maxSteps", DEFAULT_MAX_STEPS)))
        g.validate()
        return g

    @classmethod
    def load(cls, path: str | Path) -> "Grid":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def as_dict(self) -> dict[str, Any]:
        return {"experiment": self.experiment, "groups": self.groups, "seeds": self.seeds, "attacks": self.attacks,
                "epsilons": self.epsilons, "episodes": self.episodes, "workers": self.workers,
                "maxSteps": self.max_steps}

    def validate(self) -> None:
        if not self.experiment or "/" in self.experiment:
            raise ValueError("experiment must be a non-empty name without '/'")
        for g in self.groups:
            if g not in GROUPS:
                raise ValueError(f"unknown model group {g!r}")
        for s in self.seeds:
            if s not in SEEDS:
                raise ValueError(f"unknown checkpoint seed {s}")
        for a in self.attacks:
            if a not in ATTACKS or a == "none":
                raise ValueError(f"unknown attack {a!r}")
        for e in self.epsilons:
            if not math.isfinite(e) or e < 0:
                raise ValueError(f"bad epsilon {e}")
        if self.episodes < 1 or self.workers < 1 or self.max_steps < 1:
            raise ValueError("episodes, workers and maxSteps must be at least 1")


@dataclass(frozen=True)
class Episode:
    group: str
    checkpoint: int
    attack: str
    epsilon: float
    env_seed: int
    cell: str

    @property
    def model_id(self) -> str:
        return f"{self.group}/{self.checkpoint}"


def cell_name(attack: str, epsilon: float) -> str:
    return "clean" if attack == "none" or epsilon == 0 else f"{attack}-{epsilon:g}"


def plan(grid: Grid) -> list[Episode]:
    """Every episode of the grid. The clean condition (epsilon 0) runs once per model, not once per attack.
    Environment seeds 0, 1, 2, ... are the same in every condition."""
    conditions: list[tuple[str, float]] = []
    if any(e == 0 for e in grid.epsilons):
        conditions.append(("none", 0.0))
    conditions += [(a, e) for a in grid.attacks for e in grid.epsilons if e > 0]
    return [Episode(g, s, a, e, ep, cell_name(a, e))
            for g in grid.groups for s in grid.seeds for a, e in conditions for ep in range(grid.episodes)]
