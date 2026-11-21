"""The store: one SQLite file in WAL mode, written only by one writer thread (D13).

The writer waits for the first queued record, takes everything else that is queued, writes it all
in one transaction and commits. After the commit it hands the records back to the event loop by
resolving the futures returned by `submit_*`, in submission order.

Readers (REST, viewer catch-up) use their own read-only connections, one per thread.
"""

from __future__ import annotations

import asyncio
import json
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from icads.gateway.model import StepRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    reason TEXT,
    message TEXT,
    created_at TEXT NOT NULL,
    ended_at TEXT,
    protocol_version INTEGER,
    scenario TEXT,
    env_config TEXT,
    seed INTEGER,
    max_steps INTEGER,
    model_id TEXT,
    attack TEXT,
    epsilon REAL,
    pace_hz REAL,
    labels TEXT,
    road TEXT,
    obs_spec TEXT,
    action_names TEXT,
    steps INTEGER NOT NULL DEFAULT 0,
    total_reward REAL NOT NULL DEFAULT 0,
    crashed INTEGER NOT NULL DEFAULT 0,
    final_poses BLOB
);
CREATE INDEX IF NOT EXISTS runs_status ON runs(status);
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL,
    t INTEGER NOT NULL,
    obs_clean BLOB NOT NULL,
    obs_perturbed BLOB NOT NULL,
    linf REAL NOT NULL,
    action INTEGER NOT NULL,
    q BLOB NOT NULL,
    clean_action INTEGER NOT NULL,
    clean_q BLOB NOT NULL,
    reward REAL NOT NULL,
    terminated INTEGER NOT NULL,
    truncated INTEGER NOT NULL,
    crashed INTEGER NOT NULL,
    poses BLOB NOT NULL,
    perturbation_replica TEXT,
    inference_replica TEXT NOT NULL,
    perturb_ms REAL,
    infer_ms REAL NOT NULL,
    perturb_handle_ms REAL,
    infer_handle_ms REAL NOT NULL,
    gateway_ms REAL NOT NULL,
    rtt_ms REAL NOT NULL,
    committed_at REAL NOT NULL,
    PRIMARY KEY (run_id, t)
) WITHOUT ROWID;
"""

STEP_COLUMNS = (
    "run_id, t, obs_clean, obs_perturbed, linf, action, q, clean_action, clean_q, reward, terminated, "
    "truncated, crashed, poses, perturbation_replica, inference_replica, perturb_ms, infer_ms, "
    "perturb_handle_ms, infer_handle_ms, gateway_ms, rtt_ms, committed_at"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def blob(a: np.ndarray | None) -> bytes | None:
    if a is None:
        return None
    return np.ascontiguousarray(a, dtype="<f4").tobytes()


def unblob(b: bytes | None, cols: int | None = None) -> list | None:
    if b is None:
        return None
    a = np.frombuffer(b, dtype="<f4")
    if cols:
        a = a.reshape(-1, cols)
    return a.tolist()


def poses_list(a: np.ndarray) -> list[list[float]]:
    rows = np.asarray(a, np.float32).reshape(-1, 5).tolist()
    for r in rows:
        r[0] = int(r[0])
    return rows


def step_dict(r: StepRecord) -> dict[str, Any]:
    """A step as JSON-ready data: used for the log, the REST API and the step events."""
    return {
        "t": r.t,
        "obs": np.asarray(r.obs_clean, np.float32).tolist(),
        "obsPerturbed": np.asarray(r.obs_perturbed, np.float32).tolist(),
        "linf": r.linf,
        "action": r.action,
        "q": np.asarray(r.q, np.float32).tolist(),
        "cleanAction": r.clean_action,
        "cleanQ": np.asarray(r.clean_q, np.float32).tolist(),
        "reward": r.reward,
        "terminated": r.terminated,
        "truncated": r.truncated,
        "crashed": r.crashed,
        "poses": poses_list(r.poses),
        "perturbationReplica": r.perturbation_replica,
        "inferenceReplica": r.inference_replica,
        "perturbMs": r.perturb_ms,
        "inferMs": r.infer_ms,
        "perturbHandleMs": r.perturb_handle_ms,
        "inferHandleMs": r.infer_handle_ms,
        "gatewayMs": r.gateway_ms,
        "rttMs": r.rtt_ms,
        "committedAt": r.committed_at,
    }


def _step_from_row(row: sqlite3.Row) -> dict[str, Any]:
    poses = unblob(row["poses"], 5) or []
    for p in poses:
        p[0] = int(p[0])
    return {
        "t": row["t"],
        "obs": unblob(row["obs_clean"]),
        "obsPerturbed": unblob(row["obs_perturbed"]),
        "linf": row["linf"],
        "action": row["action"],
        "q": unblob(row["q"]),
        "cleanAction": row["clean_action"],
        "cleanQ": unblob(row["clean_q"]),
        "reward": row["reward"],
        "terminated": bool(row["terminated"]),
        "truncated": bool(row["truncated"]),
        "crashed": bool(row["crashed"]),
        "poses": poses,
        "perturbationReplica": row["perturbation_replica"],
        "inferenceReplica": row["inference_replica"],
        "perturbMs": row["perturb_ms"],
        "inferMs": row["infer_ms"],
        "perturbHandleMs": row["perturb_handle_ms"],
        "inferHandleMs": row["infer_handle_ms"],
        "gatewayMs": row["gateway_ms"],
        "rttMs": row["rtt_ms"],
        "committedAt": row["committed_at"],
    }


def _run_from_row(row: sqlite3.Row) -> dict[str, Any]:
    final = unblob(row["final_poses"], 5)
    if final:
        for p in final:
            p[0] = int(p[0])
    return {
        "runId": row["run_id"],
        "status": row["status"],
        "reason": row["reason"],
        "message": row["message"],
        "createdAt": row["created_at"],
        "endedAt": row["ended_at"],
        "protocolVersion": row["protocol_version"],
        "scenario": row["scenario"],
        "envConfig": json.loads(row["env_config"]) if row["env_config"] else None,
        "seed": row["seed"],
        "maxSteps": row["max_steps"],
        "modelId": row["model_id"],
        "attack": row["attack"],
        "epsilon": row["epsilon"],
        "paceHz": row["pace_hz"],
        "labels": json.loads(row["labels"]) if row["labels"] else {},
        "road": json.loads(row["road"]) if row["road"] else None,
        "obsSpec": json.loads(row["obs_spec"]) if row["obs_spec"] else None,
        "actionNames": json.loads(row["action_names"]) if row["action_names"] else None,
        "steps": row["steps"],
        "totalReward": row["total_reward"],
        "crashed": bool(row["crashed"]),
        "finalPoses": final,
    }


@dataclass
class _Create:
    row: dict[str, Any]


@dataclass
class _Finish:
    run_id: str
    status: str
    reason: str | None
    message: str | None
    steps: int
    total_reward: float
    crashed: bool
    final_poses: np.ndarray | None


_STOP = object()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue = queue.Queue()
        self._local = threading.local()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.commits = 0
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------ lifecycle
    def mark_restart(self) -> int:
        """Runs left as `running` by a previous gateway become `error`, GATEWAY_RESTART."""
        conn = sqlite3.connect(self.path)
        with conn:
            cur = conn.execute(
                "UPDATE runs SET status='error', reason='GATEWAY_RESTART', "
                "message='the gateway restarted while the run was active', ended_at=? WHERE status='running'",
                (now_iso(),))
        conn.close()
        return cur.rowcount

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._thread = threading.Thread(target=self._writer, name="store-writer", daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._thread is not None:
            self._queue.put(_STOP)
            self._thread.join(timeout=10)
            self._thread = None

    def queue_length(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------ writes (from the event loop)
    def _submit(self, item: Any) -> asyncio.Future:
        assert self._loop is not None, "Store.start() was not called"
        fut = self._loop.create_future()
        self._queue.put((item, fut))
        return fut

    def submit_create(self, row: dict[str, Any]) -> asyncio.Future:
        return self._submit(_Create(row))

    def submit_step(self, record: StepRecord) -> asyncio.Future:
        return self._submit(record)

    def submit_finish(self, run_id: str, status: str, reason: str | None, message: str | None, steps: int,
                      total_reward: float, crashed: bool, final_poses: np.ndarray | None) -> asyncio.Future:
        return self._submit(_Finish(run_id, status, reason, message, steps, total_reward, crashed, final_poses))

    # ------------------------------------------------------------ the writer thread
    def _writer(self) -> None:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        stop = False
        while not stop:
            batch = [self._queue.get()]
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if any(b is _STOP for b in batch):
                stop = True
                batch = [b for b in batch if b is not _STOP]
            if not batch:
                continue
            committed_at = time.time()
            error: BaseException | None = None
            try:
                with conn:
                    for item, _ in batch:
                        self._write(conn, item, committed_at)
                self.commits += 1
            except BaseException as e:  # report to every waiter; keep the writer alive
                error = e
            assert self._loop is not None
            try:
                self._loop.call_soon_threadsafe(self._resolve, batch, error)
            except RuntimeError:  # loop closed
                pass
        conn.close()

    @staticmethod
    def _resolve(batch: list, error: BaseException | None) -> None:
        for item, fut in batch:
            if fut.done():
                continue
            if error is None:
                fut.set_result(item)
            else:
                fut.set_exception(error)

    @staticmethod
    def _write(conn: sqlite3.Connection, item: Any, committed_at: float) -> None:
        if isinstance(item, _Create):
            r = item.row
            cols = ", ".join(r)
            conn.execute(f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(r))})", tuple(r.values()))
        elif isinstance(item, StepRecord):
            item.committed_at = committed_at
            conn.execute(
                f"INSERT INTO steps ({STEP_COLUMNS}) VALUES ({', '.join('?' * 23)})",
                (item.run_id, item.t, blob(item.obs_clean), blob(item.obs_perturbed), item.linf, item.action,
                 blob(item.q), item.clean_action, blob(item.clean_q), item.reward, int(item.terminated),
                 int(item.truncated), int(item.crashed), blob(item.poses), item.perturbation_replica,
                 item.inference_replica, item.perturb_ms, item.infer_ms, item.perturb_handle_ms,
                 item.infer_handle_ms, item.gateway_ms, item.rtt_ms, committed_at))
        elif isinstance(item, _Finish):
            conn.execute(
                "UPDATE runs SET status=?, reason=?, message=?, steps=?, total_reward=?, crashed=?, "
                "final_poses=?, ended_at=? WHERE run_id=?",
                (item.status, item.reason, item.message, item.steps, item.total_reward, int(item.crashed),
                 blob(item.final_poses), now_iso(), item.run_id))
        else:
            raise TypeError(item)

    # ------------------------------------------------------------ reads (any thread but the loop)
    def _reader(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def read_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._reader().execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return _run_from_row(row) if row else None

    def read_runs(self, status: str | None = None, model: str | None = None, attack: str | None = None,
                  experiment: str | None = None, synthetic: str = "false", limit: int = 100,
                  offset: int = 0) -> list[dict[str, Any]]:
        where, args = [], []
        for col, val in (("status", status), ("model_id", model), ("attack", attack)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        if experiment is not None:
            where.append("json_extract(labels, '$.experiment')=?")
            args.append(experiment)
        is_syn = "coalesce(json_extract(labels, '$.synthetic'), '') IN ('true', '1')"
        if synthetic == "false":
            where.append(f"NOT {is_syn}")
        elif synthetic == "true":
            where.append(is_syn)
        sql = "SELECT * FROM runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
        rows = self._reader().execute(sql, (*args, limit, offset)).fetchall()
        return [_run_from_row(r) for r in rows]

    def read_steps(self, run_id: str, after_t: int = -1) -> list[dict[str, Any]]:
        rows = self._reader().execute(
            f"SELECT {STEP_COLUMNS} FROM steps WHERE run_id=? AND t>? ORDER BY t", (run_id, after_t)).fetchall()
        return [_step_from_row(r) for r in rows]
