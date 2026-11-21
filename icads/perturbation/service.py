"""The perturbation service: the attacker (docs/ARCHITECTURE.md, section 8).

Stateless: every call is a pure function of the request, so any replica gives the same answer.
"""

from __future__ import annotations

import math
import threading
import time

import grpc
import numpy as np

from icads.attacks import ATTACKS, perturb, within_bound
from icads.policy import load_all, set_single_thread
from icads.protocol import perturbation_pb2 as pb
from icads.protocol import perturbation_pb2_grpc as rpc
from icads.protocol.reference import BOUND_TOL, OBS_SIZE
from icads.protocol.serving import default_replica_id, elapsed_ms, start_server


class PerturbationServicer(rpc.PerturbationServicer):
    def __init__(self, replica_id: str):
        set_single_thread()
        self.replica_id = replica_id
        self.nets = load_all()
        self._lock = threading.Lock()  # one CPU per replica

    def Perturb(self, request: pb.PerturbRequest, context: grpc.ServicerContext) -> pb.PerturbReply:
        start = time.perf_counter()
        net = self.nets.get(request.model_id)
        if net is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown model {request.model_id!r}")
        if request.attack not in ATTACKS:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"unknown attack {request.attack!r}")
        eps = float(request.epsilon)
        if not math.isfinite(eps) or eps < 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"bad epsilon {eps}")
        x = np.asarray(request.obs, dtype=np.float32)
        if x.shape != (OBS_SIZE,) or not np.all(np.isfinite(x)):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"observation must be {OBS_SIZE} finite values")
        with self._lock:
            out = perturb(net, request.attack, eps, request.seed, request.t, x)
        if not within_bound(x, out, eps, BOUND_TOL):
            context.abort(grpc.StatusCode.INTERNAL, "self-check failed: output not finite or outside the epsilon-ball")
        return pb.PerturbReply(obs=out.tolist(), replica_id=self.replica_id, handle_ms=elapsed_ms(start))


def serve(port: int, replica_id: str | None = None) -> tuple[grpc.Server, int]:
    servicer = PerturbationServicer(replica_id or default_replica_id(port))
    server, bound = start_server(lambda s: rpc.add_PerturbationServicer_to_server(servicer, s), port)
    if replica_id is None:
        servicer.replica_id = default_replica_id(bound)
    return server, bound
