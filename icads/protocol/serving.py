"""Shared helpers for the two stateless gRPC services and their health checks.

Plain gRPC only: no PyTorch here.
"""

from __future__ import annotations

import os
import socket
import time
from concurrent import futures
from typing import Callable

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

KEEPALIVE_OPTIONS = [
    ("grpc.keepalive_time_ms", 10_000),
    ("grpc.keepalive_timeout_ms", 5_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
    ("grpc.http2.min_ping_interval_without_data_ms", 5_000),
    ("grpc.http2.min_time_between_pings_ms", 5_000),
]
SERVER_KEEPALIVE_OPTIONS = KEEPALIVE_OPTIONS + [
    ("grpc.http2.min_recv_ping_interval_without_data_ms", 5_000),
]
# The two stateless services also close each connection after about 30 s (gracefully: calls in flight
# finish). The gateway's round-robin channel then resolves the service name again, so replicas added with
# `docker compose up --scale` are used without restarting the gateway.
SERVICE_SERVER_OPTIONS = SERVER_KEEPALIVE_OPTIONS + [
    ("grpc.max_connection_age_ms", 30_000),
    ("grpc.max_connection_age_grace_ms", 10_000),
]


def default_replica_id(port: int) -> str:
    return os.environ.get("ICADS_REPLICA_ID") or f"{socket.gethostname()}:{port}"


def start_server(add: Callable[[grpc.Server], None], port: int, workers: int = 8,
                 host: str = "0.0.0.0") -> tuple[grpc.Server, int]:
    """Start a threaded gRPC server with the standard health service. Returns (server, port)."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=workers), options=SERVICE_SERVER_OPTIONS)
    add(server)
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    bound = server.add_insecure_port(f"{host}:{port}")
    server.start()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    return server, bound


def check_health(target: str, timeout: float = 2.0) -> bool:
    """True if the gRPC health service at `target` reports SERVING."""
    try:
        with grpc.insecure_channel(target) as channel:
            reply = health_pb2_grpc.HealthStub(channel).Check(  # pyright: ignore[reportAttributeAccessIssue]
                health_pb2.HealthCheckRequest(service=""), timeout=timeout)
        return reply.status == health_pb2.HealthCheckResponse.SERVING
    except grpc.RpcError:
        return False


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def wait_forever(server: grpc.Server) -> None:
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(1)
