"""gRPC clients for the perturbation and inference services (docs/ARCHITECTURE.md, sections 6.2 and 13).

Each service is reached through one channel whose target lists every replica, with gRPC's
`round_robin` policy, so consecutive calls go to consecutive replicas. A call that fails to reach a
replica or takes longer than 5 s is retried once (round robin sends it to the next replica). A
replica that answers with an error is not retried.

Fallback (`ICADS_BALANCE=per-replica`): one channel per named replica, rotated by the gateway.
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import os
import socket
import time

import grpc
import numpy as np

from icads.gateway.model import ActReply, PerturbReply, ServiceFailed, ServiceUnavailable
from icads.protocol import inference_pb2, inference_pb2_grpc, perturbation_pb2, perturbation_pb2_grpc
from icads.protocol.serving import KEEPALIVE_OPTIONS, check_health

CALL_TIMEOUT = 5.0
RETRYABLE = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}
CHANNEL_OPTIONS = KEEPALIVE_OPTIONS + [
    ("grpc.lb_policy_name", "round_robin"),
    ("grpc.enable_retries", 0),
    # notice replicas that come back quickly
    ("grpc.initial_reconnect_backoff_ms", 200),
    ("grpc.max_reconnect_backoff_ms", 2000),
    ("grpc.dns_min_time_between_resolutions_ms", 1000),
]


def normalise_target(target: str) -> str:
    """`a:1,b:2` (a plain address list) becomes `ipv4:a:1,b:2`; anything with a scheme is kept."""
    if ":///" in target or target.startswith(("ipv4:", "ipv6:", "unix:")):
        return target
    if "," in target:
        return "ipv4:" + target
    return target


def resolve_replicas(target: str) -> list[str]:
    """Every replica address behind a target. A `dns:///name:port` target (Docker Compose) is looked up,
    so a scaled service yields one address per replica; an address list is returned as it is."""
    out: list[str] = []
    for item in split_replicas(target):
        host, _, port = item.rpartition(":")
        try:
            ipaddress.ip_address(host)
            addrs = [host]
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_STREAM)
            except (OSError, ValueError):
                continue
            addrs = sorted({info[4][0] for info in infos})
        out += [f"{a}:{port}" for a in addrs if f"{a}:{port}" not in out]
    return out


def split_replicas(target: str) -> list[str]:
    """Addresses for the per-replica fallback: `dns:///a:1` -> [`a:1`]; `ipv4:a:1,b:2` -> [`a:1`, `b:2`]."""
    t = target
    for prefix in ("dns:///", "ipv4:", "ipv6:"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return [x for x in t.split(",") if x]


class _Balanced:
    """One stub per channel; one channel in round-robin mode, one per replica in the fallback."""

    def __init__(self, name: str, target: str, stub_cls, per_replica: bool):
        self.name = name
        self.target = target
        if per_replica:
            self.channels = [grpc.aio.insecure_channel(a, options=CHANNEL_OPTIONS) for a in split_replicas(target)]
        else:
            self.channels = [grpc.aio.insecure_channel(normalise_target(target), options=CHANNEL_OPTIONS)]
        self.stubs = [stub_cls(c) for c in self.channels]
        self._next = itertools.cycle(range(len(self.stubs)))
        self.in_flight = 0
        self.calls = 0
        self.retries = 0

    async def call(self, method: str, request):
        self.in_flight += 1
        self.calls += 1
        try:
            last: grpc.aio.AioRpcError | None = None
            for attempt in range(2):
                stub = self.stubs[next(self._next)]
                try:
                    return await getattr(stub, method)(request, timeout=CALL_TIMEOUT, wait_for_ready=True)
                except grpc.aio.AioRpcError as e:
                    if e.code() not in RETRYABLE:
                        raise ServiceFailed(f"{self.name} {method}: {e.code().name}: {e.details()}") from e
                    last = e
                    if attempt == 0:
                        self.retries += 1
            assert last is not None
            raise ServiceUnavailable(self.name, f"{self.name} {method}: {last.code().name}: {last.details()}")
        finally:
            self.in_flight -= 1

    async def close(self) -> None:
        for c in self.channels:
            await c.close()


class GrpcServices:
    def __init__(self, perturbation_target: str, inference_target: str, per_replica: bool | None = None):
        if per_replica is None:
            per_replica = os.environ.get("ICADS_BALANCE", "round_robin") == "per-replica"
        self.per_replica = per_replica
        self.perturbation = _Balanced("perturbation", perturbation_target, perturbation_pb2_grpc.PerturbationStub,
                                      per_replica)
        self.inference = _Balanced("inference", inference_target, inference_pb2_grpc.InferenceStub, per_replica)

    async def perturb(self, model_id: str, attack: str, epsilon: float, seed: int, t: int,
                      obs: np.ndarray) -> PerturbReply:
        start = time.perf_counter()
        reply = await self.perturbation.call("Perturb", perturbation_pb2.PerturbRequest(
            model_id=model_id, attack=attack, epsilon=epsilon, seed=seed, t=t, obs=obs.tolist()))
        return PerturbReply(np.asarray(reply.obs, np.float32), reply.replica_id, reply.handle_ms,
                            (time.perf_counter() - start) * 1000.0)

    async def act(self, model_id: str, obs: np.ndarray, clean_obs: np.ndarray | None) -> ActReply:
        start = time.perf_counter()
        req = inference_pb2.ActRequest(model_id=model_id, obs=obs.tolist(),
                                       clean_obs=[] if clean_obs is None else clean_obs.tolist())
        reply = await self.inference.call("Act", req)
        return ActReply(int(reply.action), np.asarray(reply.q, np.float32), int(reply.clean_action),
                        np.asarray(reply.clean_q, np.float32), reply.replica_id, reply.handle_ms,
                        (time.perf_counter() - start) * 1000.0)

    async def list_models(self) -> list[dict]:
        reply = await self.inference.call("ListModels", inference_pb2.ListModelsRequest())
        return [{"id": m.id, "group": m.group, "seed": m.seed, "frame": m.frame} for m in reply.models]

    def in_flight(self) -> dict[str, int]:
        return {"perturbation": self.perturbation.in_flight, "inference": self.inference.in_flight}

    async def health(self) -> dict:
        """Each service is healthy only if every replica it resolves to passes the gRPC health check."""
        out: dict = {"replicas": {}}
        for b in (self.perturbation, self.inference):
            addresses = await asyncio.to_thread(resolve_replicas, b.target)
            ok = await asyncio.gather(*(asyncio.to_thread(check_health, a, 2.0) for a in addresses))
            out[b.name] = bool(addresses) and all(ok)
            out["replicas"][b.name] = len(addresses)
        return out

    async def close(self) -> None:
        await self.perturbation.close()
        await self.inference.close()
