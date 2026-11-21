"""`icads vehicle`: play one episode through the gateway, in lockstep.

Hello -> Welcome, then per step: Obs -> Action -> env.step -> Result, until Summary.
"""

from __future__ import annotations

import json
import queue
import sys
import time
from dataclasses import dataclass, field

import grpc

from icads.protocol import gateway_pb2 as pb
from icads.protocol import gateway_pb2_grpc as rpc
from icads.protocol.reference import ACTION_NAMES, ENV_CONFIG, FEATURES, OBS_ROWS, PROTOCOL_VERSION, SCENARIO
from icads.protocol.serving import KEEPALIVE_OPTIONS
from icads.vehicle.env import VehicleIds, feature_ranges, flatten, make_env, road_geometry


@dataclass
class RunSettings:
    model_id: str
    seed: int = 0
    attack: str = "none"
    epsilon: float = 0.0
    pace_hz: float = 0.0
    max_steps: int = 60
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class RunOutcome:
    run_id: str
    status: str
    reason: str
    message: str
    steps: int
    total_reward: float
    crashed: bool
    actions: list[int] = field(default_factory=list)

    def as_line(self) -> str:
        return json.dumps({"runId": self.run_id, "status": self.status, "reason": self.reason,
                           "message": self.message, "steps": self.steps, "totalReward": self.total_reward,
                           "crashed": self.crashed})


def _poses(raw) -> list[pb.Pose]:
    return [pb.Pose(id=i, x=x, y=y, heading=h, speed=s) for i, x, y, h, s in raw]


class _Stream:
    """A request queue feeding one Drive stream, and its response iterator."""

    def __init__(self, stub: rpc.GatewayStub):
        self._q: queue.Queue = queue.Queue()
        self.responses = stub.Drive(iter(self._q.get, None))

    def send(self, msg: pb.VehicleMsg) -> None:
        self._q.put(msg)

    def recv(self) -> pb.GatewayMsg:
        return next(self.responses)

    def close(self) -> None:
        self._q.put(None)


def _summary(run_id: str, s: pb.Summary, actions: list[int]) -> RunOutcome:
    return RunOutcome(s.run_id or run_id, s.status, s.reason, s.message, s.steps, s.total_reward, s.crashed,
                      actions)


def run_vehicle(gateway: str, settings: RunSettings, verbose: bool = False) -> RunOutcome:
    env = make_env()
    obs, _ = env.reset(seed=settings.seed)
    ids = VehicleIds()
    ranges = feature_ranges(env)
    hello = pb.Hello(
        protocol_version=PROTOCOL_VERSION, scenario=SCENARIO, env_config_json=json.dumps(ENV_CONFIG),
        seed=settings.seed, max_steps=settings.max_steps, model_id=settings.model_id, attack=settings.attack,
        epsilon=settings.epsilon, pace_hz=settings.pace_hz,
        obs_spec=pb.ObsSpec(features=list(FEATURES), vehicles=OBS_ROWS, absolute=False,
                            ranges={k: pb.Range(low=lo, high=hi) for k, (lo, hi) in ranges.items()}),
        action_names=list(ACTION_NAMES),
        road=pb.Road(lanes=[pb.Lane(polyline=[pb.Point(x=x, y=y) for x, y in lane["polyline"]],
                                    width=lane["width"]) for lane in road_geometry(env)]),
        labels=settings.labels)

    channel = grpc.insecure_channel(gateway, options=KEEPALIVE_OPTIONS)
    stream = _Stream(rpc.GatewayStub(channel))
    actions: list[int] = []
    run_id = ""
    try:
        stream.send(pb.VehicleMsg(hello=hello))
        first = stream.recv()
        if first.WhichOneof("msg") == "summary":
            return _summary("", first.summary, actions)
        run_id = first.welcome.run_id
        if verbose:
            print(f"run {run_id}: {settings.model_id} {settings.attack} eps={settings.epsilon} seed={settings.seed}",
                  file=sys.stderr, flush=True)
        period = 1.0 / settings.pace_hz if settings.pace_hz > 0 else 0.0
        t = 0
        while True:
            step_start = time.monotonic()
            sent = time.perf_counter()
            stream.send(pb.VehicleMsg(obs=pb.Obs(t=t, obs=flatten(obs).tolist(), poses=_poses(ids.poses(env)))))
            reply = stream.recv()
            if reply.WhichOneof("msg") == "summary":
                return _summary(run_id, reply.summary, actions)
            rtt_ms = (time.perf_counter() - sent) * 1000.0
            action = reply.action.action
            actions.append(action)
            obs, reward, terminated, truncated, info = env.step(action)
            crashed = bool(info.get("crashed", False))
            last = terminated or truncated or t + 1 >= settings.max_steps
            result = pb.Result(t=t, reward=float(reward), terminated=terminated, truncated=truncated,
                               crashed=crashed, rtt_ms=rtt_ms,
                               final_poses=_poses(ids.poses(env)) if last else [])
            stream.send(pb.VehicleMsg(result=result))
            if last:
                reply = stream.recv()
                return _summary(run_id, reply.summary, actions)
            if period:  # pacing never changes a stored value: it only waits between steps
                time.sleep(max(0.0, period - (time.monotonic() - step_start)))
            t += 1
    except grpc.RpcError as e:
        code = e.code()  # pyright: ignore[reportAttributeAccessIssue]
        return RunOutcome(run_id, "error", "GATEWAY_UNAVAILABLE", f"{code.name}: {e.details()}",  # pyright: ignore[reportAttributeAccessIssue]
                          len(actions), 0.0, False, actions)
    except StopIteration:
        return RunOutcome(run_id, "error", "GATEWAY_CLOSED", "the gateway closed the stream", len(actions), 0.0,
                          False, actions)
    finally:
        stream.close()
        channel.close()
        env.close()


def settings_from_job(job: dict) -> RunSettings:
    """One job line of `icads vehicle --jobs`: {"model", "seed", "attack", "epsilon", "paceHz", "maxSteps", "labels"}."""
    return RunSettings(model_id=str(job["model"]), seed=int(job.get("seed", 0)), attack=str(job.get("attack", "none")),
                       epsilon=float(job.get("epsilon", 0.0)), pace_hz=float(job.get("paceHz", 0.0)),
                       max_steps=int(job.get("maxSteps", 60)), labels={str(k): str(v) for k, v in
                                                                      job.get("labels", {}).items()})


def serve_jobs(gateway: str, lines, out) -> int:
    """Worker mode: play one episode per JSON line read, and write one outcome line per episode.
    Used by the experiment runner, so a worker process pays highway-env's start-up cost only once."""
    for line in lines:
        if not line.strip():
            continue
        try:
            outcome = run_vehicle(gateway, settings_from_job(json.loads(line)))
            out.write(outcome.as_line() + "\n")
        except (ValueError, KeyError, TypeError) as e:
            out.write(json.dumps({"runId": "", "status": "error", "reason": "BAD_JOB", "message": str(e)}) + "\n")
        out.flush()
    return 0
