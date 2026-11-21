"""The vehicle link over gRPC: converts messages and passes them to the run logic.

No run logic here (D21): everything is decided in `icads.gateway.core.Gateway`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator

import grpc
import numpy as np

from icads.gateway.core import Gateway
from icads.gateway.model import ActionOut, HelloData, ObsData, ResultData, SummaryOut, WelcomeOut
from icads.protocol import gateway_pb2 as pb
from icads.protocol import gateway_pb2_grpc as rpc


def poses_array(poses) -> np.ndarray:
    return np.array([[p.id, p.x, p.y, p.heading, p.speed] for p in poses], dtype=np.float32).reshape(-1, 5)


def hello_from_pb(m: pb.Hello) -> HelloData:
    road = {"lanes": [{"polyline": [[pt.x, pt.y] for pt in lane.polyline], "width": lane.width}
                      for lane in m.road.lanes]}
    return HelloData(
        protocol_version=m.protocol_version, scenario=m.scenario, env_config_json=m.env_config_json,
        seed=m.seed, max_steps=m.max_steps, model_id=m.model_id, attack=m.attack, epsilon=m.epsilon,
        pace_hz=m.pace_hz, obs_features=list(m.obs_spec.features), obs_vehicles=m.obs_spec.vehicles,
        obs_absolute=m.obs_spec.absolute,
        obs_ranges={k: (r.low, r.high) for k, r in m.obs_spec.ranges.items()},
        action_names=list(m.action_names), road=road, labels=dict(m.labels))


def obs_from_pb(m: pb.Obs) -> ObsData:
    return ObsData(t=m.t, obs=np.asarray(m.obs, dtype=np.float32), poses=poses_array(m.poses))


def result_from_pb(m: pb.Result) -> ResultData:
    return ResultData(t=m.t, reward=m.reward, terminated=m.terminated, truncated=m.truncated, crashed=m.crashed,
                      rtt_ms=m.rtt_ms, final_poses=poses_array(m.final_poses) if len(m.final_poses) else None)


def to_pb(out: WelcomeOut | ActionOut | SummaryOut) -> pb.GatewayMsg:
    if isinstance(out, WelcomeOut):
        return pb.GatewayMsg(welcome=pb.Welcome(run_id=out.run_id))
    if isinstance(out, ActionOut):
        return pb.GatewayMsg(action=pb.Action(t=out.t, action=out.action))
    return pb.GatewayMsg(summary=pb.Summary(run_id=out.run_id, status=out.status, reason=out.reason,
                                            message=out.message, steps=out.steps, total_reward=out.total_reward,
                                            crashed=out.crashed))


class GatewayServicer(rpc.GatewayServicer):
    def __init__(self, gateway: Gateway):
        self.gw = gateway

    async def Drive(self, request_iterator, context: grpc.aio.ServicerContext) -> AsyncIterator[pb.GatewayMsg]:
        gw = self.gw
        it = request_iterator.__aiter__()
        try:
            first = await asyncio.wait_for(it.__anext__(), gw.vehicle_timeout)
        except (StopAsyncIteration, asyncio.TimeoutError):
            return
        kind = first.WhichOneof("msg")
        if kind != "hello":
            yield to_pb(await gw.on_unexpected(None, kind or "an empty message"))
            return
        out = await gw.on_hello(hello_from_pb(first.hello))
        yield to_pb(out)
        if isinstance(out, SummaryOut):
            return
        run_id = out.run_id
        reader: asyncio.Task | None = None
        pushed: asyncio.Task | None = None
        try:
            while True:
                read_task = reader = reader or asyncio.ensure_future(it.__anext__())
                push_task = pushed = pushed or asyncio.ensure_future(gw.next_push(run_id))
                done, _ = await asyncio.wait({read_task, push_task}, timeout=gw.vehicle_timeout,
                                             return_when=asyncio.FIRST_COMPLETED)
                if push_task in done:  # an abort
                    yield to_pb(push_task.result())
                    return
                if not done:
                    summary = await gw.on_timeout(run_id)
                    if summary is not None:
                        yield to_pb(summary)
                    return
                try:
                    msg = read_task.result()
                except (StopAsyncIteration, asyncio.CancelledError, grpc.RpcError):
                    await gw.on_disconnect(run_id)
                    return
                reader = None
                kind = msg.WhichOneof("msg")
                if kind == "obs":
                    reply = await gw.on_obs(run_id, obs_from_pb(msg.obs))
                elif kind == "result":
                    reply = await gw.on_result(run_id, result_from_pb(msg.result))
                else:
                    reply = await gw.on_unexpected(run_id, kind or "an empty message")
                if reply is not None:
                    yield to_pb(reply)
                    if isinstance(reply, SummaryOut):
                        return
                elif (ended := gw.ended(run_id)) is not None:
                    # ended while this message was handled (an abort): deliver its summary
                    yield to_pb(ended)
                    return
        finally:
            for task in (reader, pushed):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
            if gw.ended(run_id) is None:
                # the stream was cancelled under us (the vehicle went away)
                await asyncio.shield(gw.on_disconnect(run_id))
