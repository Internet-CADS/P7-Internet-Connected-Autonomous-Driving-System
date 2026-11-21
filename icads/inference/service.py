"""The inference service: the agent (docs/ARCHITECTURE.md, section 9).

`Act` runs the network once on one observation, or on the perturbed and the clean one together.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import grpc
import numpy as np
from torch import nn

from icads.policy import greedy, list_models, load_all, q_values, set_single_thread
from icads.protocol import inference_pb2 as pb
from icads.protocol import inference_pb2_grpc as rpc
from icads.protocol.reference import OBS_SIZE
from icads.protocol.serving import default_replica_id, elapsed_ms, start_server


@dataclass(frozen=True)
class ActResult:
    action: int
    q: np.ndarray
    clean_action: int
    clean_q: np.ndarray


def act_batch(net: nn.Module, obs: np.ndarray, clean_obs: np.ndarray | None) -> ActResult:
    """One forward pass on [obs] or [obs, clean_obs]."""
    batch = obs[None] if clean_obs is None else np.stack([obs, clean_obs])
    q = q_values(net, batch)
    a = greedy(q)
    if clean_obs is None:
        return ActResult(int(a[0]), q[0], int(a[0]), q[0])
    return ActResult(int(a[0]), q[0], int(a[1]), q[1])


class InferenceServicer(rpc.InferenceServicer):
    def __init__(self, replica_id: str):
        set_single_thread()
        self.replica_id = replica_id
        self.nets = load_all()
        self.models = list_models()
        self._lock = threading.Lock()  # one CPU per replica: one forward pass at a time

    def Act(self, request: pb.ActRequest, context: grpc.ServicerContext) -> pb.ActReply:
        start = time.perf_counter()
        net = self.nets.get(request.model_id)
        if net is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown model {request.model_id!r}")
        obs = np.asarray(request.obs, dtype=np.float32)
        clean = np.asarray(request.clean_obs, dtype=np.float32) if len(request.clean_obs) else None
        if obs.shape != (OBS_SIZE,) or (clean is not None and clean.shape != (OBS_SIZE,)):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"observations must have {OBS_SIZE} values")
        with self._lock:
            r = act_batch(net, obs, clean)
        return pb.ActReply(action=r.action, q=r.q.tolist(), clean_action=r.clean_action,
                           clean_q=r.clean_q.tolist(), replica_id=self.replica_id,
                           handle_ms=elapsed_ms(start))

    def ListModels(self, request: pb.ListModelsRequest, context: grpc.ServicerContext) -> pb.ModelList:
        return pb.ModelList(models=[pb.Model(id=m.id, group=m.group, seed=m.seed, frame=m.frame)
                                    for m in self.models])


def serve(port: int, replica_id: str | None = None) -> tuple[grpc.Server, int]:
    servicer = InferenceServicer(replica_id or default_replica_id(port))
    server, bound = start_server(lambda s: rpc.add_InferenceServicer_to_server(servicer, s), port)
    if replica_id is None:
        servicer.replica_id = default_replica_id(bound)
    return server, bound
