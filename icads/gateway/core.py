"""The gateway's run logic: admission, checks, the two service calls, recording and the live feed.

Plain Python on plain data (`icads.gateway.model`); it knows nothing about gRPC (D21). A transport
adapter calls `on_hello`, `on_obs`, `on_result`, `on_disconnect` and `on_timeout`, sends what they
return, and also forwards anything that appears on the session's `pushed` queue (an abort).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from icads.gateway.model import (
    ActionOut,
    ActReply,
    HelloData,
    ObsData,
    PerturbReply,
    ResultData,
    ServiceFailed,
    ServiceUnavailable,
    StepRecord,
    SummaryOut,
    WelcomeOut,
)
from icads.gateway.store import Store, now_iso, poses_list, step_dict
from icads.protocol.reference import (
    ACTION_NAMES,
    ATTACKS,
    BOUND_TOL,
    DEFAULT_MAX_STEPS,
    FEATURES,
    MODEL_IDS,
    OBS_ROWS,
    OBS_SIZE,
    PROTOCOL_VERSION,
    RANGE_TOL,
    SCENARIO,
    env_config_matches,
    is_attacked,
)

log = logging.getLogger("icads.gateway")

BUFFER_STEPS = 256
VIEWER_QUEUE = 512


class Services(Protocol):
    async def perturb(self, model_id: str, attack: str, epsilon: float, seed: int, t: int,
                      obs: np.ndarray) -> PerturbReply: ...

    async def act(self, model_id: str, obs: np.ndarray, clean_obs: np.ndarray | None) -> ActReply: ...


# ---------------------------------------------------------------- live feed
@dataclass(frozen=True)
class Event:
    """One server-sent event, serialised once and shared by every viewer."""
    name: str
    data: bytes
    id: int | None = None

    def encode(self) -> bytes:
        head = f"id: {self.id}\n" if self.id is not None else ""
        return f"{head}event: {self.name}\n".encode() + b"data: " + self.data + b"\n\n"


class Viewer:
    """A bounded queue for one viewer. A viewer that falls behind is dropped (it reconnects)."""

    def __init__(self, maxsize: int = VIEWER_QUEUE):
        self.queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize)
        self.dropped = False

    def offer(self, ev: Event | None) -> bool:
        if self.dropped:
            return False
        try:
            self.queue.put_nowait(ev)
            return True
        except asyncio.QueueFull:
            self.dropped = True
            # make room for the close marker so the reader wakes up and disconnects
            try:
                while True:
                    self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.queue.put_nowait(None)
            return False


@dataclass
class Feed:
    """The committed steps of one active run, and its viewers."""
    buffer: deque[tuple[int, Event]] = field(default_factory=lambda: deque(maxlen=BUFFER_STEPS))
    viewers: set[Viewer] = field(default_factory=set)
    ended: Event | None = None


# ---------------------------------------------------------------- sessions
@dataclass
class Pending:
    obs: ObsData
    received: float
    perturbed: np.ndarray
    linf: float
    act: ActReply
    perturb: PerturbReply | None
    gateway_ms: float = 0.0


@dataclass
class Session:
    run_id: str
    hello: HelloData
    epsilon: float
    attacked: bool
    max_steps: int
    next_t: int = 0
    pending: Pending | None = None
    steps: int = 0
    total_reward: float = 0.0
    crashed: bool = False
    ended: SummaryOut | None = None
    last_message: float = field(default_factory=time.monotonic)
    pushed: asyncio.Queue[SummaryOut] = field(default_factory=asyncio.Queue)
    feed: Feed = field(default_factory=Feed)
    meta: bytes = b""


class Refused(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def check_hello(h: HelloData) -> None:
    """Raise `Refused` if the Hello does not match the reference settings."""
    if h.protocol_version != PROTOCOL_VERSION:
        raise Refused("PROTOCOL_VERSION", f"protocol version {h.protocol_version}, expected {PROTOCOL_VERSION}")
    if h.scenario != SCENARIO:
        raise Refused("SPEC_MISMATCH", f"scenario {h.scenario!r}, expected {SCENARIO!r}")
    if not env_config_matches(h.env_config_json):
        raise Refused("SPEC_MISMATCH", "environment settings differ from the reference")
    if list(h.obs_features) != list(FEATURES) or h.obs_vehicles != OBS_ROWS or h.obs_absolute:
        raise Refused("SPEC_MISMATCH", "observation layout differs from the reference")
    if not {"x", "y", "vx", "vy"} <= set(h.obs_ranges):
        raise Refused("SPEC_MISMATCH", "observation scaling is missing")
    if list(h.action_names) != list(ACTION_NAMES):
        raise Refused("SPEC_MISMATCH", "action names differ from the reference")
    if h.model_id not in MODEL_IDS:
        raise Refused("UNKNOWN_MODEL", f"unknown model {h.model_id!r}")
    if h.attack not in ATTACKS:
        raise Refused("UNKNOWN_ATTACK", f"unknown attack {h.attack!r}")
    if not math.isfinite(h.epsilon) or h.epsilon < 0:
        raise Refused("BAD_EPSILON", f"epsilon must be finite and not negative, got {h.epsilon}")


def display_epsilon(eps: float) -> float:
    """Epsilon arrives as float32; store and show it as the decimal the user typed."""
    return float(f"{eps:.6g}")


def run_summary(session: Session, status: str, created_at: str | None = None) -> dict[str, Any]:
    h = session.hello
    return {"runId": session.run_id, "status": status, "modelId": h.model_id, "attack": h.attack,
            "epsilon": display_epsilon(h.epsilon), "seed": h.seed, "labels": h.labels,
            "createdAt": created_at, "steps": session.steps, "crashed": session.crashed,
            "reason": session.ended.reason if session.ended else None}


class Gateway:
    def __init__(self, store: Store, services: Services, max_runs: int = 64,
                 vehicle_timeout: float = 30.0):
        self.store = store
        self.services = services
        self.max_runs = max_runs
        self.vehicle_timeout = vehicle_timeout
        self.sessions: dict[str, Session] = {}
        self.run_listeners: set[Viewer] = set()
        self.refused = 0
        self._recently_ended: OrderedDict[str, SummaryOut] = OrderedDict()

    # ------------------------------------------------------------ vehicle link
    async def on_hello(self, h: HelloData) -> WelcomeOut | SummaryOut:
        if len(self.sessions) >= self.max_runs:
            self.refused += 1
            return SummaryOut("", "refused", "OVERLOADED", f"the gateway is at its limit of {self.max_runs} runs")
        try:
            check_hello(h)
        except Refused as r:
            self.refused += 1
            return SummaryOut("", "refused", r.reason, r.message)
        run_id = "r-" + uuid.uuid4().hex[:12]
        max_steps = h.max_steps or DEFAULT_MAX_STEPS
        h.max_steps = max_steps
        s = Session(run_id, h, float(h.epsilon), is_attacked(h.attack, h.epsilon), max_steps)
        self.sessions[run_id] = s
        created_at = now_iso()
        obs_spec = {"features": list(h.obs_features), "vehicles": h.obs_vehicles, "absolute": h.obs_absolute,
                    "ranges": {k: list(v) for k, v in h.obs_ranges.items()}}
        settings = {"seed": h.seed, "maxSteps": max_steps, "modelId": h.model_id, "attack": h.attack,
                    "epsilon": display_epsilon(h.epsilon), "paceHz": h.pace_hz, "labels": h.labels,
                    "scenario": h.scenario}
        s.meta = json.dumps({"runId": run_id, "status": "running", "settings": settings, "road": h.road,
                             "obsSpec": obs_spec, "actionNames": list(h.action_names),
                             "createdAt": created_at}).encode()
        row = dict(run_id=run_id, status="running", created_at=created_at, protocol_version=h.protocol_version,
                   scenario=h.scenario, env_config=h.env_config_json, seed=h.seed, max_steps=max_steps,
                   model_id=h.model_id, attack=h.attack, epsilon=display_epsilon(h.epsilon),
                   pace_hz=float(h.pace_hz), labels=json.dumps(h.labels, sort_keys=True), road=json.dumps(h.road),
                   obs_spec=json.dumps(obs_spec), action_names=json.dumps(list(h.action_names)))
        try:
            await self.store.submit_create(row)
        except BaseException:
            self.sessions.pop(run_id, None)  # never stored: free its slot
            raise
        self._publish_run(run_summary(s, "running", created_at))
        return WelcomeOut(run_id)

    async def on_obs(self, run_id: str, o: ObsData) -> ActionOut | SummaryOut | None:
        received = time.perf_counter()
        s = self._session(run_id)
        if s is None:
            return None
        s.last_message = time.monotonic()
        if s.pending is not None or o.t != s.next_t:
            return await self.end(s, "error", "PROTOCOL_VIOLATION",
                                  f"expected Obs t={s.next_t}" if s.pending is None else f"expected Result t={s.next_t}")
        x = np.asarray(o.obs, dtype=np.float32)
        if x.shape != (OBS_SIZE,):
            return await self.end(s, "error", "BAD_OBS_SHAPE", f"{x.size} values, expected {OBS_SIZE}")
        if not np.all(np.isfinite(x)):
            return await self.end(s, "error", "NON_FINITE_OBS", "the observation contains NaN or Inf")
        if float(np.max(np.abs(x))) > 1.0 + RANGE_TOL:
            return await self.end(s, "error", "OBS_OUT_OF_RANGE", "a clean value is outside [-1, 1]")
        o.obs = x
        h = s.hello

        pert: PerturbReply | None = None
        perturbed = x
        linf = 0.0
        try:
            if s.attacked:
                pert = await self.services.perturb(h.model_id, h.attack, s.epsilon, h.seed, o.t, x)
                if s.ended:
                    return None
                perturbed = np.asarray(pert.obs, dtype=np.float32)
                if perturbed.shape != (OBS_SIZE,) or not np.all(np.isfinite(perturbed)):
                    return await self.end(s, "error", "SERVICE_ERROR", "the perturbation is not 50 finite values")
                linf = float(np.max(np.abs(perturbed - x)))
                if linf > s.epsilon + BOUND_TOL:
                    return await self.end(s, "error", "SERVICE_ERROR",
                                          f"perturbation of {linf:.6g} exceeds epsilon {s.epsilon:.6g}")
            differs = pert is not None and not np.array_equal(perturbed, x)
            act = await self.services.act(h.model_id, perturbed, x if differs else None)
        except ServiceUnavailable as e:
            reason = "PERTURBATION_UNAVAILABLE" if e.service == "perturbation" else "INFERENCE_UNAVAILABLE"
            return await self.end(s, "error", reason, str(e))
        except ServiceFailed as e:
            return await self.end(s, "error", "SERVICE_ERROR", str(e))
        if s.ended:
            return None
        if not 0 <= act.action < len(ACTION_NAMES):
            return await self.end(s, "error", "SERVICE_ERROR", f"action {act.action} out of range")
        s.pending = Pending(o, received, perturbed, linf, act, pert)
        s.pending.gateway_ms = (time.perf_counter() - received) * 1000.0
        return ActionOut(o.t, act.action)

    async def on_result(self, run_id: str, r: ResultData) -> SummaryOut | None:
        s = self._session(run_id)
        if s is None:
            return None
        s.last_message = time.monotonic()
        p = s.pending
        if p is None or r.t != s.next_t:
            return await self.end(s, "error", "PROTOCOL_VIOLATION", f"unexpected Result t={r.t}")
        s.pending = None
        s.next_t += 1
        s.steps += 1
        s.total_reward += float(r.reward)
        s.crashed = s.crashed or bool(r.crashed)
        a = p.act
        record = StepRecord(
            run_id=s.run_id, t=r.t, obs_clean=p.obs.obs, obs_perturbed=p.perturbed, linf=p.linf,
            action=a.action, q=np.asarray(a.q, np.float32), clean_action=a.clean_action,
            clean_q=np.asarray(a.clean_q, np.float32), reward=float(r.reward), terminated=bool(r.terminated),
            truncated=bool(r.truncated), crashed=bool(r.crashed),
            poses=np.asarray(p.obs.poses, np.float32).reshape(-1, 5),
            perturbation_replica=p.perturb.replica_id if p.perturb else None, inference_replica=a.replica_id,
            perturb_ms=p.perturb.call_ms if p.perturb else None, infer_ms=a.call_ms,
            perturb_handle_ms=p.perturb.handle_ms if p.perturb else None, infer_handle_ms=a.handle_ms,
            gateway_ms=p.gateway_ms, rtt_ms=float(r.rtt_ms))
        fut = self.store.submit_step(record)
        fut.add_done_callback(lambda f, s=s: self._on_step_committed(s, f))
        if r.terminated or r.truncated or s.steps >= s.max_steps:
            return await self.end(s, "finished", "", "", final_poses=r.final_poses)
        return None

    async def on_unexpected(self, run_id: str | None, kind: str) -> SummaryOut:
        """A message out of place on the vehicle link: before Hello (`run_id` None: refused, nothing stored),
        or a second Hello during a run (the run ends)."""
        if run_id is None:
            self.refused += 1
            return SummaryOut("", "refused", "PROTOCOL_VIOLATION", f"the first message must be Hello, not {kind}")
        s = self._session(run_id)
        if s is None:
            return self.ended(run_id) or SummaryOut(run_id, "error", "PROTOCOL_VIOLATION", "unknown run")
        return await self.end(s, "error", "PROTOCOL_VIOLATION", f"unexpected {kind} during the run")

    def ended(self, run_id: str) -> SummaryOut | None:
        """The summary of a run that has ended (kept for recently ended runs), or None while it runs."""
        s = self.sessions.get(run_id)
        if s is not None:
            return s.ended
        return self._recently_ended.get(run_id)

    async def next_push(self, run_id: str) -> SummaryOut:
        """Wait for a summary pushed to the vehicle out of turn (an abort)."""
        s = self.sessions.get(run_id)
        if s is None:
            ended = self.ended(run_id)
            if ended is not None:
                return ended
            raise KeyError(run_id)
        return await s.pushed.get()

    async def on_disconnect(self, run_id: str) -> None:
        s = self._session(run_id)
        if s is not None:
            await self.end(s, "error", "VEHICLE_DISCONNECTED", "the vehicle's stream broke")

    async def on_timeout(self, run_id: str) -> SummaryOut | None:
        s = self._session(run_id)
        if s is None:
            return None
        return await self.end(s, "error", "VEHICLE_TIMEOUT",
                              f"no message from the vehicle for {self.vehicle_timeout:g} s")

    async def abort(self, run_id: str) -> SummaryOut | None:
        """Abort from the page. The summary is also pushed to the vehicle."""
        s = self._session(run_id)
        if s is None:
            return None
        summary = await self.end(s, "aborted", "ABORTED_BY_USER", "aborted from the web page")
        s.pushed.put_nowait(summary)
        return summary

    def _session(self, run_id: str) -> Session | None:
        s = self.sessions.get(run_id)
        if s is None or s.ended is not None:
            return None
        return s

    async def end(self, s: Session, status: str, reason: str, message: str,
                  final_poses: np.ndarray | None = None) -> SummaryOut:
        if s.ended is not None:
            return s.ended
        s.ended = SummaryOut(s.run_id, status, reason, message, s.steps, s.total_reward, s.crashed)
        if status != "finished":
            log.warning("run %s ended: %s %s %s", s.run_id, status, reason, message)
        try:
            await self.store.submit_finish(s.run_id, status, reason or None, message or None, s.steps,
                                           s.total_reward, s.crashed, final_poses)
        finally:
            self.sessions.pop(s.run_id, None)
            self._recently_ended[s.run_id] = s.ended
            while len(self._recently_ended) > 4096:
                self._recently_ended.popitem(last=False)
        end_data = {"status": status, "reason": reason or None, "message": message or None, "steps": s.steps,
                    "totalReward": s.total_reward, "crashed": s.crashed,
                    "finalPoses": poses_list(final_poses) if final_poses is not None else None}
        ev = Event("end", json.dumps(end_data).encode(), id=s.steps)
        s.feed.ended = ev
        for v in list(s.feed.viewers):
            v.offer(ev)
            v.offer(None)
        self._publish_run(run_summary(s, status))
        return s.ended

    # ------------------------------------------------------------ live feed
    def _on_step_committed(self, s: Session, fut: asyncio.Future) -> None:
        if fut.cancelled() or fut.exception() is not None:
            log.error("step of run %s was not stored: %s", s.run_id, fut.exception())
            return
        record: StepRecord = fut.result()
        d = step_dict(record)
        ev = Event("step", json.dumps(d).encode(), id=record.t)
        s.feed.buffer.append((record.t, ev))
        for v in list(s.feed.viewers):
            if not v.offer(ev):
                s.feed.viewers.discard(v)

    def _publish_run(self, summary: dict[str, Any]) -> None:
        ev = Event("run", json.dumps(summary).encode())
        for v in list(self.run_listeners):
            if not v.offer(ev):
                self.run_listeners.discard(v)

    def active_feed(self, run_id: str) -> Session | None:
        """The live session of a run, until its end is stored and published. A run that has ended for the
        vehicle but whose final state is not yet committed is still followed live, so a viewer joining in
        that window gets the real `end` event and not a stale one read from the store."""
        s = self.sessions.get(run_id)
        if s is None or s.feed.ended is not None:
            return None
        return s

    def with_live_totals(self, run: dict[str, Any]) -> dict[str, Any]:
        """A stored run, with the step count and totals of its live session if it is still running
        (the store only writes them when the run ends)."""
        s = self.sessions.get(run["runId"])
        if s is not None and run["status"] == "running":
            run = {**run, "steps": s.steps, "totalReward": s.total_reward, "crashed": s.crashed}
        return run

    async def shutdown(self) -> None:
        """The gateway is stopping: end every active run as `error`, GATEWAY_RESTART (not as a vehicle
        disconnect, which closing the streams would otherwise report) and tell each vehicle."""
        for s in list(self.sessions.values()):
            if s.ended is None:
                summary = await self.end(s, "error", "GATEWAY_RESTART", "the gateway shut down during the run")
                s.pushed.put_nowait(summary)

    def close_viewers(self) -> None:
        """Shutdown: tell every event stream to close (browsers reconnect to the next gateway)."""
        for v in [*self.run_listeners, *(v for s in self.sessions.values() for v in s.feed.viewers)]:
            v.offer(None)

    def viewer_count(self) -> int:
        return len(self.run_listeners) + sum(len(s.feed.viewers) for s in self.sessions.values())
