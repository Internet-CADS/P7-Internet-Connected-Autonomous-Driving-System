"""Vehicle-client worker processes for the experiment runner.

Each worker is a long-lived `icads vehicle --jobs` process: it pays highway-env's start-up cost once and
then plays one episode per job line. The runner only talks to it through stdin and stdout, so the
experiments component never imports the vehicle component (docs/ARCHITECTURE.md, section 17).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from typing import Any


class VehicleProcess:
    def __init__(self, gateway: str):
        self.gateway = gateway
        self.proc: subprocess.Popen[str] | None = None

    def _start(self) -> subprocess.Popen[str]:
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "icads.cli", "vehicle", "--gateway", self.gateway, "--jobs"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        return self.proc

    def play(self, job: dict[str, Any]) -> dict[str, Any]:
        proc = self._start()
        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write(json.dumps(job) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
        except (BrokenPipeError, OSError) as e:
            line = ""
            err = str(e)
        else:
            err = "the vehicle worker exited"
        if not line:  # the worker died; the next job starts a new one
            self.close()
            return {"runId": "", "status": "error", "reason": "WORKER_ERROR", "message": err}
        return json.loads(line)

    def close(self) -> None:
        if self.proc is not None:
            if self.proc.stdin:
                self.proc.stdin.close()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None


class VehiclePool:
    """One vehicle process per worker thread, started on first use."""

    def __init__(self, gateway: str):
        self.gateway = gateway
        self._local = threading.local()
        self._all: list[VehicleProcess] = []
        self._lock = threading.Lock()

    def play(self, job: dict[str, Any]) -> dict[str, Any]:
        proc = getattr(self._local, "proc", None)
        if proc is None:
            proc = self._local.proc = VehicleProcess(self.gateway)
            with self._lock:
                self._all.append(proc)
        return proc.play(job)

    def close(self) -> None:
        for p in self._all:
            p.close()
