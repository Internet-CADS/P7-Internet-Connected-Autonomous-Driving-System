"""Issue 08 through the whole system: broken streams, silence, killed replicas."""

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import grpc
import numpy as np
import pytest

from icads.policy import load_model, q_values
from icads.protocol import gateway_pb2 as pb
from icads.protocol import gateway_pb2_grpc as rpc
from icads.protocol.reference import ACTION_NAMES, ENV_CONFIG, FEATURES, PROTOCOL_VERSION, SCENARIO
from icads.vehicle.client import RunSettings, run_vehicle
from tests.system import running_system

pytestmark = pytest.mark.slow


def raw_hello(**over):
    h = pb.Hello(protocol_version=PROTOCOL_VERSION, scenario=SCENARIO, env_config_json=json.dumps(ENV_CONFIG),
                 seed=1, max_steps=60, model_id="sa_dqn/1", attack="none",
                 obs_spec=pb.ObsSpec(features=list(FEATURES), vehicles=10, absolute=False,
                                     ranges={k: pb.Range(low=-1, high=1) for k in ("x", "y", "vx", "vy")}),
                 action_names=list(ACTION_NAMES))
    for k, v in over.items():
        setattr(h, k, v)
    return h


def wait_for(fn, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture(scope="module")
def system(tmp_path_factory):
    with running_system(tmp_path_factory.mktemp("fail"), vehicle_timeout=1.0) as gw:
        yield gw


def open_stream(system):
    channel = grpc.insecure_channel(system.grpc_target)
    q: queue.Queue = queue.Queue()
    call = rpc.GatewayStub(channel).Drive(iter(q.get, None))
    return channel, q, call


def test_bad_hello_over_the_wire_is_refused(system):
    channel, q, call = open_stream(system)
    q.put(pb.VehicleMsg(hello=raw_hello(model_id="nope/1")))
    reply = next(call)
    assert (reply.summary.status, reply.summary.reason) == ("refused", "UNKNOWN_MODEL")
    q.put(None)
    channel.close()


def test_a_broken_stream_ends_the_run(system):
    channel, q, call = open_stream(system)
    q.put(pb.VehicleMsg(hello=raw_hello()))
    run_id = next(call).welcome.run_id
    q.put(pb.VehicleMsg(obs=pb.Obs(t=0, obs=[0.0] * 50)))
    assert next(call).WhichOneof("msg") == "action"
    call.cancel()
    channel.close()
    run = wait_for(lambda: (r := system.get(f"/v1/runs/{run_id}"))["status"] != "running" and r)
    assert (run["status"], run["reason"]) == ("error", "VEHICLE_DISCONNECTED")


def test_silence_ends_the_run(system):
    channel, q, call = open_stream(system)
    q.put(pb.VehicleMsg(hello=raw_hello()))
    run_id = next(call).welcome.run_id
    reply = next(call)  # nothing sent: the gateway gives up after the (shortened) timeout
    assert (reply.summary.status, reply.summary.reason) == ("error", "VEHICLE_TIMEOUT")
    assert system.get(f"/v1/runs/{run_id}")["reason"] == "VEHICLE_TIMEOUT"
    q.put(None)
    channel.close()


def test_abort_reaches_the_vehicle(system):
    result = {}
    t = threading.Thread(target=lambda: result.update(
        out=run_vehicle(system.grpc_target, RunSettings("sa_dqn/1", seed=2, pace_hz=5.0))))
    t.start()
    run = wait_for(lambda: next((r for r in system.get("/v1/runs?status=running")["runs"]
                                 if r["modelId"] == "sa_dqn/1" and r["seed"] == 2), None))
    time.sleep(0.5)
    reply = system.delete(f"/v1/runs/{run['runId']}")
    assert reply["status"] == "aborted"
    t.join(10)
    assert result["out"].status == "aborted"
    assert system.get(f"/v1/runs/{run['runId']}")["status"] == "aborted"


def test_killing_one_replica_of_each_service_fails_no_run(tmp_path):
    with running_system(tmp_path, replicas=2) as system:
        servers = system.servers  # [inf-1, per-1, inf-2, per-2]

        def play(seed):
            return run_vehicle(system.grpc_target, RunSettings("vanilla_dqn/2", seed=seed, attack="pgd",
                                                               epsilon=0.005, pace_hz=10.0, max_steps=30))

        with ThreadPoolExecutor(4) as ex:
            futures = [ex.submit(play, s) for s in range(4)]
            time.sleep(1.0)
            servers[1].stop(None)  # a perturbation replica
            time.sleep(0.7)
            servers[0].stop(None)  # an inference replica
            outs = [f.result(60) for f in futures]
        assert [o.status for o in outs] == ["finished"] * 4, [o.as_line() for o in outs]

        net = load_model("vanilla_dqn/2")
        replicas = set()
        for o in outs:
            steps = [s for s in system.get(f"/v1/runs/{o.run_id}/log") if s["type"] == "step"]
            replicas |= {s["inferenceReplica"] for s in steps} | {s["perturbationReplica"] for s in steps}
            # every applied action is the one the agent gives for the stored perturbed observation
            q = q_values(net, np.asarray([s["obsPerturbed"] for s in steps], np.float32))
            assert [s["action"] for s in steps] == q.argmax(axis=1).tolist() == o.actions
        assert replicas == {"inference-1", "inference-2", "perturbation-1", "perturbation-2"}


def test_hello_out_of_place_over_the_wire(system):
    channel, q, call = open_stream(system)
    q.put(pb.VehicleMsg(obs=pb.Obs(t=0, obs=[0.0] * 50)))
    reply = next(call)
    assert (reply.summary.status, reply.summary.reason) == ("refused", "PROTOCOL_VIOLATION")
    q.put(None)
    channel.close()

    channel, q, call = open_stream(system)
    q.put(pb.VehicleMsg(hello=raw_hello()))
    run_id = next(call).welcome.run_id
    q.put(pb.VehicleMsg(hello=raw_hello()))
    reply = next(call)
    assert (reply.summary.status, reply.summary.reason) == ("error", "PROTOCOL_VIOLATION")
    assert system.get(f"/v1/runs/{run_id}")["reason"] == "PROTOCOL_VIOLATION"
    q.put(None)
    channel.close()
