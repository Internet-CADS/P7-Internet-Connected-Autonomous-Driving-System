import grpc
import numpy as np
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from icads.inference.service import serve
from icads.policy import load_model, q_values
from icads.protocol import inference_pb2 as pb
from icads.protocol import inference_pb2_grpc as rpc


@pytest.fixture(scope="module")
def stub():
    server, port = serve(port=0, replica_id="inf-test")
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    yield rpc.InferenceStub(channel), channel, port
    channel.close()
    server.stop(None)


def test_act_on_one_observation_repeats_it_as_clean(stub, reference_obs):
    s, _, _ = stub
    x = reference_obs[0]
    reply = s.Act(pb.ActRequest(model_id="wocar_dqn/1", obs=x.tolist()))
    expected = q_values(load_model("wocar_dqn/1"), x[None])[0]
    np.testing.assert_allclose(reply.q, expected, rtol=1e-6)
    assert reply.action == int(np.argmax(expected))
    assert reply.clean_action == reply.action
    assert list(reply.clean_q) == list(reply.q)
    assert reply.replica_id == "inf-test"
    assert reply.handle_ms >= 0


def test_act_on_perturbed_and_clean(stub, reference_obs):
    s, _, _ = stub
    reply = s.Act(pb.ActRequest(model_id="vanilla_dqn/2", obs=reference_obs[1].tolist(),
                                clean_obs=reference_obs[6].tolist()))
    q = q_values(load_model("vanilla_dqn/2"), reference_obs[[1, 6]])
    np.testing.assert_allclose(reply.q, q[0], rtol=1e-6)
    np.testing.assert_allclose(reply.clean_q, q[1], rtol=1e-6)
    assert (reply.action, reply.clean_action) == (int(q[0].argmax()), int(q[1].argmax()))


def test_unknown_model_and_bad_shape_are_errors(stub):
    s, _, _ = stub
    with pytest.raises(grpc.RpcError) as e:
        s.Act(pb.ActRequest(model_id="nope/1", obs=[0.0] * 50))
    assert e.value.code() == grpc.StatusCode.NOT_FOUND
    with pytest.raises(grpc.RpcError) as e:
        s.Act(pb.ActRequest(model_id="sa_dqn/1", obs=[0.0] * 49))
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_list_models(stub):
    s, _, _ = stub
    models = s.ListModels(pb.ListModelsRequest()).models
    assert len(models) == 21
    m = {x.id: x for x in models}["sa_dqn/4"]
    assert (m.group, m.seed, m.frame) == ("sa_dqn", 4, 1_600_000)


def test_health(stub):
    _, channel, port = stub
    from icads.protocol.serving import check_health
    assert check_health(f"127.0.0.1:{port}")
    reply = health_pb2_grpc.HealthStub(channel).Check(  # pyright: ignore[reportAttributeAccessIssue]
        health_pb2.HealthCheckRequest(service=""))
    assert reply.status == health_pb2.HealthCheckResponse.SERVING


def test_tie_break_lowest_index():
    from icads.inference.service import act_batch
    import torch
    lin = torch.nn.Linear(50, 5)
    with torch.no_grad():
        lin.weight.zero_()
        lin.bias.copy_(torch.tensor([0.0, 1.0, 1.0, 1.0, 0.5]))
    net = torch.nn.Sequential(lin)
    r = act_batch(net, np.zeros(50, np.float32), None)
    assert r.action == 1 and r.clean_action == 1
