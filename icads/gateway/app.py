"""The gateway process: one asyncio event loop running the gRPC vehicle link and the HTTP server."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass

import grpc
from aiohttp import web

from icads.gateway.core import Gateway
from icads.gateway.grpc_adapter import GatewayServicer
from icads.gateway.http import Http
from icads.gateway.services import GrpcServices
from icads.gateway.store import Store
from icads.protocol import gateway_pb2_grpc
from icads.protocol.serving import SERVER_KEEPALIVE_OPTIONS, check_health

log = logging.getLogger("icads.gateway")


@dataclass
class GatewayConfig:
    inference: str = "127.0.0.1:50052"
    perturbation: str = "127.0.0.1:50053"
    db: str = "data/runs.sqlite"
    max_runs: int = 64
    host: str = "0.0.0.0"
    grpc_port: int = 50051
    http_port: int = 8000
    wait_for_services: float = 120.0
    vehicle_timeout: float = 30.0

    @classmethod
    def from_args(cls, a: argparse.Namespace) -> "GatewayConfig":
        env = os.environ
        return cls(
            inference=a.inference or env.get("ICADS_INFERENCE", cls.inference),
            perturbation=a.perturbation or env.get("ICADS_PERTURBATION", cls.perturbation),
            db=a.db or env.get("ICADS_DB", cls.db),
            max_runs=a.max_runs or int(env.get("ICADS_MAX_RUNS", cls.max_runs)),
            host=a.host, grpc_port=a.grpc_port, http_port=a.http_port,
            vehicle_timeout=float(env.get("ICADS_VEHICLE_TIMEOUT", cls.vehicle_timeout)),
        )


class RunningGateway:
    """Started servers, for `main` and for tests."""

    def __init__(self, cfg: GatewayConfig):
        self.cfg = cfg
        self.store = Store(cfg.db)
        self.services = GrpcServices(cfg.perturbation, cfg.inference)
        self.gateway = Gateway(self.store, self.services, cfg.max_runs, cfg.vehicle_timeout)
        self.http = Http(self.gateway, self.services)
        self.grpc_server: grpc.aio.Server | None = None
        self.runner: web.AppRunner | None = None
        self.grpc_port = cfg.grpc_port
        self.http_port = cfg.http_port
        self._lag_task: asyncio.Task | None = None

    async def wait_for_services(self) -> None:
        """Block until every named replica of both services passes its health check."""
        from icads.gateway.services import resolve_replicas

        deadline = time.monotonic() + self.cfg.wait_for_services
        while True:
            inference = await asyncio.to_thread(resolve_replicas, self.cfg.inference)
            perturbation = await asyncio.to_thread(resolve_replicas, self.cfg.perturbation)
            targets = inference + perturbation
            ok = await asyncio.gather(*(asyncio.to_thread(check_health, t, 1.0) for t in targets))
            if inference and perturbation and all(ok):
                return
            if time.monotonic() > deadline:
                raise RuntimeError(f"services not healthy: {[t for t, k in zip(targets, ok) if not k]}")
            log.info("waiting for services: %s", [t for t, k in zip(targets, ok) if not k])
            await asyncio.sleep(1.0)

    async def start(self) -> None:
        restarted = self.store.mark_restart()
        if restarted:
            log.warning("%d runs left running by a previous gateway set to error, GATEWAY_RESTART", restarted)
        loop = asyncio.get_running_loop()
        self.store.start(loop)
        await self.wait_for_services()
        self.grpc_server = grpc.aio.server(options=SERVER_KEEPALIVE_OPTIONS)
        gateway_pb2_grpc.add_GatewayServicer_to_server(GatewayServicer(self.gateway), self.grpc_server)
        self.grpc_port = self.grpc_server.add_insecure_port(f"{self.cfg.host}:{self.cfg.grpc_port}")
        await self.grpc_server.start()
        self.runner = web.AppRunner(self.http.app(), access_log=None, shutdown_timeout=3.0)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.cfg.host, self.cfg.http_port)
        await site.start()
        if self.cfg.http_port == 0:
            self.http_port = self.runner.addresses[0][1]
        self._lag_task = asyncio.create_task(self._measure_lag())
        log.info("gateway: gRPC on %d, HTTP on %d", self.grpc_port, self.http_port)

    async def _measure_lag(self, interval: float = 0.1) -> None:
        loop = asyncio.get_running_loop()
        while True:
            start = loop.time()
            await asyncio.sleep(interval)
            lag = (loop.time() - start - interval) * 1000.0
            self.http.loop_lag_ms = max(0.0, 0.8 * self.http.loop_lag_ms + 0.2 * lag)

    async def stop(self) -> None:
        if self._lag_task:
            self._lag_task.cancel()
        await self.gateway.shutdown()  # vehicles get their Summary during the grace period below
        self.gateway.close_viewers()
        if self.grpc_server:
            await self.grpc_server.stop(2)
        if self.runner:
            await self.runner.cleanup()
        await self.services.close()
        self.store.close()


async def serve(cfg: GatewayConfig) -> None:
    g = RunningGateway(cfg)
    await g.start()
    print(f"gateway listening: gRPC {g.grpc_port}, HTTP {g.http_port}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()
    await g.stop()


def main(a: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(serve(GatewayConfig.from_args(a)))
