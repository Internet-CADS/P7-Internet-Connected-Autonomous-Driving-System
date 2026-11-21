"""highway-v0 with the reference settings, and what the vehicle reports about it."""

from __future__ import annotations

from typing import Any

import gymnasium as gym
import highway_env  # noqa: F401  (registers highway-v0)
import numpy as np

from icads.protocol.reference import ENV_CONFIG, SCENARIO


def make_env() -> gym.Env:
    return gym.make(SCENARIO, config=ENV_CONFIG)


def flatten(obs: Any) -> np.ndarray:
    """10 x 5 -> 50 values, row by row (as the models repository's adapter does)."""
    return np.asarray(obs, dtype=np.float32).reshape(-1)


def feature_ranges(env: gym.Env) -> dict[str, tuple[float, float]]:
    """The observation scaling, read from the environment after reset."""
    ranges = env.unwrapped.observation_type.features_range  # pyright: ignore[reportAttributeAccessIssue]
    return {k: (float(v[0]), float(v[1])) for k, v in ranges.items()}


def road_geometry(env: gym.Env) -> list[dict[str, Any]]:
    """Each lane as a polyline (its two ends for straight lanes) and a width."""
    lanes = []
    network = env.unwrapped.road.network  # pyright: ignore[reportAttributeAccessIssue]
    for _from, tos in network.graph.items():
        for _to, lane_list in tos.items():
            for lane in lane_list:
                if hasattr(lane, "start") and hasattr(lane, "end"):
                    pts = [lane.start, lane.end]
                else:  # a curved lane: sample it
                    pts = [lane.position(s, 0) for s in np.linspace(0, lane.length, 50)]
                lanes.append({"polyline": [[float(p[0]), float(p[1])] for p in pts],
                              "width": float(lane.width_at(0))})
    return lanes


class VehicleIds:
    """A small stable id per vehicle, given the first time it is seen. The ego is 0.

    Keyed by Python's `id()`, so each vehicle seen is also kept referenced: a removed vehicle's memory
    can then never be reused by a new one that would inherit its id. One episode has few vehicles.
    """

    def __init__(self) -> None:
        self._ids: dict[int, int] = {}
        self._seen: list[object] = []

    def poses(self, env: gym.Env) -> list[tuple[int, float, float, float, float]]:
        u = env.unwrapped
        ego = u.vehicle  # pyright: ignore[reportAttributeAccessIssue]
        others = [v for v in u.road.vehicles if v is not ego]  # pyright: ignore[reportAttributeAccessIssue]
        out = []
        for v in [ego, *others]:
            key = id(v)
            if key not in self._ids:
                self._ids[key] = len(self._ids)
                self._seen.append(v)
            out.append((self._ids[key], float(v.position[0]), float(v.position[1]), float(v.heading),
                        float(v.speed)))
        return out
