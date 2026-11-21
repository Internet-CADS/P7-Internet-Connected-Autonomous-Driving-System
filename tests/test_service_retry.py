"""Issue 08: the gateway's service clients retry a lost call once on another replica, never an error reply."""

import socket
import time

import grpc
import numpy as np
import pytest

from icads.gateway import services as services_mod
from icads.gateway.model import ServiceFailed, ServiceUnavailable
from icads.gateway.services import GrpcServices
from icads.protocol import inference_pb2 as pb
from icads.protocol import inference_pb2_grpc as rpc
from icads.protocol.serving import KEEPALIVE_OPTIONS, SERVER_KEEPALIVE_OPTIONS, start_server


class Scripted(rpc.InferenceServicer):
    def __init__(self, name, mode="ok", delay=0.0):
        self.name, self.mode, self.delay, self.calls = name, mode, delay, 0

    def Act(self, request, context):
        self.calls += 1
        time.sleep(self.delay)
        if self.mode == "error":
            context.abort(grpc.StatusCode.INTERNAL, "deterministic failure")
        return pb.ActReply(action=1, q=[0, 1, 0, 0, 0], clean_action=1, clean_q=[0, 1, 0, 0, 0],
                           replica_id=self.name)


def serve(servicer):
    server, port = start_server(lambda s: rpc.add_InferenceServicer_to_server(servicer, s), 0, host="127.0.0.1")
    return server, f"127.0.0.1:{port}"


def dead_address():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    addr = f"127.0.0.1:{s.getsockname()[1]}"
    s.close()
    return addr


X = np.zeros(50, np.float32)


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch):
    """Calls wait for a connection up to the timeout; keep dead-replica tests fast."""
    monkeypatch.setattr(services_mod, "CALL_TIMEOUT", 0.5)


@pytest.mark.parametrize("per_replica", [False, True])
async def test_a_lost_call_is_retried_on_another_replica(per_replica):
    good = Scripted("good")
    server, addr = serve(good)
    svc = GrpcServices(dead_address(), f"{dead_address()},{addr}", per_replica=per_replica)
    try:
        for _ in range(4):
            reply = await svc.act("m", X, None)
            assert reply.replica_id == "good"
    finally:
        await svc.close()
        server.stop(None)


async def test_two_lost_calls_end_with_unavailable():
    svc = GrpcServices(dead_address(), f"{dead_address()},{dead_address()}")
    try:
        with pytest.raises(ServiceUnavailable) as e:
            await svc.act("m", X, None)
        assert e.value.service == "inference"
        with pytest.raises(ServiceUnavailable) as e:
            await svc.perturb("m", "pgd", 0.01, 0, 0, X)
        assert e.value.service == "perturbation"
    finally:
        await svc.close()


async def test_an_error_reply_is_not_retried():
    bad = Scripted("bad", mode="error")
    server, addr = serve(bad)
    svc = GrpcServices(dead_address(), addr)
    try:
        with pytest.raises(ServiceFailed):
            await svc.act("m", X, None)
        assert bad.calls == 1
    finally:
        await svc.close()
        server.stop(None)


async def test_a_slow_call_times_out_and_is_retried(monkeypatch):
    monkeypatch.setattr(services_mod, "CALL_TIMEOUT", 0.3)
    slow, fast = Scripted("slow", delay=1.0), Scripted("fast")
    s1, a1 = serve(slow)
    s2, a2 = serve(fast)
    svc = GrpcServices(dead_address(), f"{a1},{a2}", per_replica=True)
    try:
        replies = [await svc.act("m", X, None) for _ in range(2)]
        assert {r.replica_id for r in replies} == {"fast"}
        assert slow.calls >= 1
    finally:
        await svc.close()
        s1.stop(None)
        s2.stop(None)


def test_keepalive_pings_are_configured_on_both_ends():
    client = dict(KEEPALIVE_OPTIONS)
    server = dict(SERVER_KEEPALIVE_OPTIONS)
    assert client["grpc.keepalive_time_ms"] <= 30_000 and client["grpc.keepalive_permit_without_calls"] == 1
    assert server["grpc.keepalive_time_ms"] <= 30_000
    assert dict(services_mod.CHANNEL_OPTIONS)["grpc.keepalive_time_ms"] == client["grpc.keepalive_time_ms"]


def test_replicas_are_resolved_from_every_kind_of_target():
    from icads.gateway.services import resolve_replicas
    assert resolve_replicas("ipv4:127.0.0.1:1,127.0.0.1:2") == ["127.0.0.1:1", "127.0.0.1:2"]
    assert resolve_replicas("127.0.0.1:1,127.0.0.1:2") == ["127.0.0.1:1", "127.0.0.1:2"]
    assert "127.0.0.1:50052" in resolve_replicas("dns:///localhost:50052")
    assert resolve_replicas("dns:///no-such-host.invalid:1") == []


async def test_health_needs_every_replica_serving():
    up = Scripted("up")
    server, addr = serve(up)
    one_down = GrpcServices(addr, f"{addr},{dead_address()}")
    all_up = GrpcServices(addr, addr)
    try:
        h = await one_down.health()
        assert h["perturbation"] is True and h["inference"] is False
        assert h["replicas"] == {"perturbation": 1, "inference": 2}
        assert (await all_up.health())["inference"] is True
    finally:
        await one_down.close()
        await all_up.close()
        server.stop(None)


def test_services_close_old_connections_so_new_replicas_are_found():
    opts = dict(SERVER_KEEPALIVE_OPTIONS)
    from icads.protocol.serving import SERVICE_SERVER_OPTIONS
    svc = dict(SERVICE_SERVER_OPTIONS)
    assert 0 < svc["grpc.max_connection_age_ms"] <= 60_000
    assert svc["grpc.max_connection_age_grace_ms"] > services_mod.CALL_TIMEOUT * 1000
    assert "grpc.max_connection_age_ms" not in opts  # never on the gateway's long vehicle streams
