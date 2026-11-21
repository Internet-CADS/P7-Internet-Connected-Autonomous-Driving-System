"""Plain data exchanged with the gateway's run logic. No gRPC types here (D21)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ---------------------------------------------------------------- vehicle -> gateway
@dataclass
class HelloData:
    protocol_version: int
    scenario: str
    env_config_json: str
    seed: int
    max_steps: int
    model_id: str
    attack: str
    epsilon: float
    pace_hz: float
    obs_features: list[str]
    obs_vehicles: int
    obs_absolute: bool
    obs_ranges: dict[str, tuple[float, float]]
    action_names: list[str]
    road: dict[str, Any]
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class ObsData:
    t: int
    obs: np.ndarray            # float32, should be 50 values
    poses: np.ndarray          # float32 [n, 5]: id, x, y, heading, speed; ego first


@dataclass
class ResultData:
    t: int
    reward: float
    terminated: bool
    truncated: bool
    crashed: bool
    rtt_ms: float
    final_poses: np.ndarray | None = None


# ---------------------------------------------------------------- gateway -> vehicle
@dataclass(frozen=True)
class WelcomeOut:
    run_id: str


@dataclass(frozen=True)
class ActionOut:
    t: int
    action: int


@dataclass(frozen=True)
class SummaryOut:
    run_id: str
    status: str            # finished, aborted, error, refused
    reason: str = ""
    message: str = ""
    steps: int = 0
    total_reward: float = 0.0
    crashed: bool = False


# ---------------------------------------------------------------- service replies
@dataclass(frozen=True)
class PerturbReply:
    obs: np.ndarray
    replica_id: str
    handle_ms: float
    call_ms: float


@dataclass(frozen=True)
class ActReply:
    action: int
    q: np.ndarray
    clean_action: int
    clean_q: np.ndarray
    replica_id: str
    handle_ms: float
    call_ms: float


class ServiceUnavailable(Exception):
    """A call failed to reach a replica twice (or timed out twice)."""

    def __init__(self, service: str, message: str):
        super().__init__(message)
        self.service = service  # "perturbation" or "inference"


class ServiceFailed(Exception):
    """A replica answered with an error. Not retried: the call is deterministic."""


# ---------------------------------------------------------------- stored step
@dataclass
class StepRecord:
    run_id: str
    t: int
    obs_clean: np.ndarray
    obs_perturbed: np.ndarray
    linf: float
    action: int
    q: np.ndarray
    clean_action: int
    clean_q: np.ndarray
    reward: float
    terminated: bool
    truncated: bool
    crashed: bool
    poses: np.ndarray
    perturbation_replica: str | None
    inference_replica: str
    perturb_ms: float | None
    infer_ms: float
    perturb_handle_ms: float | None
    infer_handle_ms: float
    gateway_ms: float
    rtt_ms: float
    committed_at: float = 0.0
