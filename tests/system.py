"""A whole system in one test process: services, gateway (own event loop thread), vehicle clients."""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from icads.gateway.app import GatewayConfig, RunningGateway


class GatewayThread:
    def __init__(self, cfg: GatewayConfig):
        self.cfg = cfg
        self.loop = asyncio.new_event_loop()
        self.g: RunningGateway | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.servers: list = []  # in-process service servers, set by running_system

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.g = RunningGateway(self.cfg)
            self.loop.run_until_complete(self.g.start())
        except BaseException as e:  # noqa: BLE001
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        self.loop.run_forever()

    def start(self) -> "GatewayThread":
        self.thread.start()
        self._ready.wait(60)
        if self._error:
            raise self._error
        return self

    def call(self, coro, timeout: float = 30):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self) -> None:
        if self.g is not None:
            self.call(self.g.stop())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)

    @property
    def grpc_target(self) -> str:
        assert self.g
        return f"127.0.0.1:{self.g.grpc_port}"

    @property
    def http(self) -> str:
        assert self.g
        return f"http://127.0.0.1:{self.g.http_port}"

    def get(self, path: str) -> Any:
        with urllib.request.urlopen(self.http + path, timeout=30) as r:
            body = r.read().decode()
        if path.endswith("/log"):
            return [json.loads(line) for line in body.splitlines()]
        return json.loads(body)

    def delete(self, path: str) -> Any:
        req = urllib.request.Request(self.http + path, method="DELETE")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())


@contextmanager
def running_system(tmp: Path, replicas: int = 1, max_runs: int = 64, vehicle_timeout: float = 30.0):
    from icads.inference.service import serve as serve_inference
    from icads.perturbation.service import serve as serve_perturbation

    servers = []
    inf_ports, per_ports = [], []
    for i in range(replicas):
        s, p = serve_inference(0, f"inference-{i + 1}")
        servers.append(s)
        inf_ports.append(p)
        s, p = serve_perturbation(0, f"perturbation-{i + 1}")
        servers.append(s)
        per_ports.append(p)
    cfg = GatewayConfig(
        inference=",".join(f"127.0.0.1:{p}" for p in inf_ports),
        perturbation=",".join(f"127.0.0.1:{p}" for p in per_ports),
        db=str(tmp / "runs.sqlite"), max_runs=max_runs, host="127.0.0.1", grpc_port=0, http_port=0,
        wait_for_services=20, vehicle_timeout=vehicle_timeout)
    gw = GatewayThread(cfg).start()
    gw.servers = servers
    try:
        yield gw
    finally:
        gw.stop()
        for s in servers:
            s.stop(None)
