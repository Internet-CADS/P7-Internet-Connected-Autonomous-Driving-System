"""Plain-data helpers for driving the gateway's run logic without gRPC."""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from icads.gateway.model import ActReply, HelloData, ObsData, PerturbReply, ResultData
from icads.protocol.reference import ACTION_NAMES, ENV_CONFIG, FEATURES, OBS_ROWS, PROTOCOL_VERSION, SCENARIO

RANGES = {"x": (-200.0, 200.0), "y": (-16.0, 16.0), "vx": (-80.0, 80.0), "vy": (-80.0, 80.0)}


def hello(**over) -> HelloData:
    base: dict[str, Any] = dict(
        protocol_version=PROTOCOL_VERSION, scenario=SCENARIO, env_config_json=json.dumps(ENV_CONFIG),
        seed=7, max_steps=60, model_id="wocar_dqn/1", attack="none", epsilon=0.0, pace_hz=0.0,
        obs_features=list(FEATURES), obs_vehicles=OBS_ROWS, obs_absolute=False, obs_ranges=dict(RANGES),
        action_names=list(ACTION_NAMES),
        road={"lanes": [{"polyline": [[0.0, 4.0 * i], [10000.0, 4.0 * i]], "width": 4.0} for i in range(4)]},
        labels={},
    )
    base.update(over)
    return HelloData(**base)


def obs(t: int, seed: int = 0) -> ObsData:
    rng = np.random.default_rng([seed, t])
    x = rng.uniform(-1, 1, 50).astype(np.float32)
    poses = np.array([[0, 100.0 + t * 25, 4.0, 0.0, 25.0], [1, 130.0 + t * 24, 8.0, 0.0, 24.0]], np.float32)
    return ObsData(t=t, obs=x, poses=poses)


def result(t: int, done: bool = False, crashed: bool = False, final: bool = False) -> ResultData:
    return ResultData(t=t, reward=0.5, terminated=done, truncated=False, crashed=crashed, rtt_ms=3.0,
                      final_poses=np.zeros((2, 5), np.float32) if final else None)


class FakeServices:
    """Stands in for the gRPC clients: a deterministic Act and a shifting Perturb."""

    def __init__(self, shift: float | None = None):
        self.shift = shift
        self.perturb_calls = []
        self.act_calls = []

    @staticmethod
    def _q(x: np.ndarray) -> np.ndarray:
        return np.array([x[0], x[1], x[2], x[3], x[4]], np.float32)

    async def perturb(self, model_id, attack, epsilon, seed, t, obs):
        self.perturb_calls.append((model_id, attack, epsilon, seed, t))
        shift = epsilon if self.shift is None else self.shift
        out = (obs + np.float32(shift)).astype(np.float32)
        return PerturbReply(obs=out, replica_id="p1", handle_ms=1.0, call_ms=1.5)

    async def act(self, model_id, obs, clean_obs):
        self.act_calls.append((model_id, obs.copy(), None if clean_obs is None else clean_obs.copy()))
        q = self._q(obs)
        cq = q if clean_obs is None else self._q(clean_obs)
        return ActReply(action=int(np.argmax(q)), q=q, clean_action=int(np.argmax(cq)), clean_q=cq,
                        replica_id="i1", handle_ms=0.5, call_ms=0.8)

    async def list_models(self):
        return []
