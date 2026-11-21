"""Reference settings shared by every component.

The environment settings are the `env_config` of the models repository's rev3 configuration
(`entries/05_wocar_dqn_r3/config_highway_*.json`; the three groups share it). The gateway refuses
a run whose settings differ from these.

This module is plain Python: the vehicle client imports it and must never load PyTorch.
"""

from __future__ import annotations

import json
import math
from typing import Any

PROTOCOL_VERSION = 1
SCENARIO = "highway-v0"

ENV_CONFIG: dict[str, Any] = {
    "lanes_count": 4,
    "vehicles_count": 15,
    "duration": 60,
    "simulation_frequency": 5,
    "policy_frequency": 1,
    "collision_reward": -2.0,
    "right_lane_reward": 0.1,
    "high_speed_reward": 0.4,
    "lane_change_reward": 0.0,
    "reward_speed_range": [20, 30],
    "normalize_reward": False,
    "offroad_terminal": True,
    "action": {"type": "DiscreteMetaAction"},
    "observation": {
        "type": "Kinematics",
        "vehicles_count": 10,
        "features": ["presence", "x", "y", "vx", "vy"],
        "absolute": False,
        "normalize": True,
        "order": "sorted",
    },
}

FEATURES: tuple[str, ...] = ("presence", "x", "y", "vx", "vy")
OBS_ROWS = 10
OBS_SIZE = OBS_ROWS * len(FEATURES)  # 50
ACTION_NAMES: tuple[str, ...] = ("LANE_LEFT", "IDLE", "LANE_RIGHT", "FASTER", "SLOWER")
NUM_ACTIONS = len(ACTION_NAMES)
DEFAULT_MAX_STEPS = 60

ATTACKS: tuple[str, ...] = ("none", "random_linf", "pgd", "minbest", "worst_q")
EPSILON_GRID: tuple[float, ...] = (0.0, 0.0025, 0.005, 0.0075, 0.01)

# Tolerances used by the gateway and the perturbation service.
BOUND_TOL = 1e-6
RANGE_TOL = 1e-6

GROUPS: dict[str, int] = {"vanilla_dqn": 1_500_000, "sa_dqn": 1_600_000, "wocar_dqn": 1_450_000}
SEEDS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)
MODEL_IDS: tuple[str, ...] = tuple(f"{g}/{s}" for g in GROUPS for s in SEEDS)

STATUSES = ("running", "finished", "aborted", "error", "refused")


def canonical_json(value: Any) -> str:
    """JSON with sorted keys and no spaces: used to compare settings."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def env_config_matches(config_json: str) -> bool:
    try:
        return canonical_json(json.loads(config_json)) == canonical_json(ENV_CONFIG)
    except (ValueError, TypeError):
        return False


def is_attacked(attack: str, epsilon: float) -> bool:
    """Whether a run calls the perturbation service at all."""
    return attack != "none" and math.isfinite(epsilon) and epsilon > 0.0
