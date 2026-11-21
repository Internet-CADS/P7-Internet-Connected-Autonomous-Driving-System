import grpc
import numpy as np
import pytest

from icads.attacks import perturb
from icads.perturbation.service import serve
from icads.policy import load_model
from icads.protocol import perturbation_pb2 as pb
from icads.protocol import perturbation_pb2_grpc as rpc
from icads.protocol.serving import check_health


@pytest.fixture(scope="module")
def service():
    server, port = serve(port=0, replica_id="per-test")
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    yield rpc.PerturbationStub(channel), port
    channel.close()
    server.stop(None)


def test_perturb_matches_the_attack_function(service, reference_obs):
    stub, _ = service
    x = reference_obs[4]
    reply = stub.Perturb(pb.PerturbRequest(model_id="sa_dqn/2", attack="worst_q", epsilon=0.005,
                                           seed=3, t=17, obs=x.tolist()))
    expected = perturb(load_model("sa_dqn/2"), "worst_q", np.float32(0.005).item(), 3, 17, x)
    np.testing.assert_array_equal(np.asarray(reply.obs, np.float32), expected)
    assert reply.replica_id == "per-test" and reply.handle_ms >= 0
    again = stub.Perturb(pb.PerturbRequest(model_id="sa_dqn/2", attack="worst_q", epsilon=0.005,
                                           seed=3, t=17, obs=x.tolist()))
    assert list(again.obs) == list(reply.obs)


@pytest.mark.parametrize("request_kwargs,code", [
    (dict(model_id="nope/1", attack="pgd", epsilon=0.01), grpc.StatusCode.NOT_FOUND),
    (dict(model_id="sa_dqn/1", attack="fgsm", epsilon=0.01), grpc.StatusCode.INVALID_ARGUMENT),
    (dict(model_id="sa_dqn/1", attack="pgd", epsilon=-0.01), grpc.StatusCode.INVALID_ARGUMENT),
])
def test_bad_requests(service, request_kwargs, code):
    stub, _ = service
    with pytest.raises(grpc.RpcError) as e:
        stub.Perturb(pb.PerturbRequest(seed=0, t=0, obs=[0.0] * 50, **request_kwargs))
    assert e.value.code() == code


def test_self_check_fails_the_call(service, monkeypatch):
    import icads.perturbation.service as svc
    monkeypatch.setattr(svc, "perturb", lambda *a, **k: np.full(50, 0.5, np.float32))
    stub, _ = service
    with pytest.raises(grpc.RpcError) as e:
        stub.Perturb(pb.PerturbRequest(model_id="sa_dqn/1", attack="pgd", epsilon=0.01, seed=0, t=0,
                                       obs=[0.0] * 50))
    assert e.value.code() == grpc.StatusCode.INTERNAL


def test_health(service):
    _, port = service
    assert check_health(f"127.0.0.1:{port}")
