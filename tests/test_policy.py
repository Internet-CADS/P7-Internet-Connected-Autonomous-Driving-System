import numpy as np
import pytest
import torch

from icads.policy import UnknownModel, build_network, greedy, list_models, load_model, q_values
from icads.protocol.reference import MODEL_IDS


def test_every_checkpoint_loads_and_matches_the_repository_loader(reference, reference_obs):
    assert len(MODEL_IDS) == 21
    for model_id in MODEL_IDS:
        q = q_values(load_model(model_id), reference_obs)
        np.testing.assert_allclose(q, np.asarray(reference["q"][model_id]), rtol=1e-5, atol=1e-5)


def test_network_shape():
    net = build_network()
    shapes = [tuple(p.shape) for p in net.parameters()]
    assert shapes == [(128, 50), (128,), (128, 128), (128,), (5, 128), (5,)]


def test_list_models_has_the_peak_frames():
    frames = {m.group: m.frame for m in list_models()}
    assert frames == {"vanilla_dqn": 1_500_000, "sa_dqn": 1_600_000, "wocar_dqn": 1_450_000}
    assert all(m.path.exists() for m in list_models())


def test_unknown_model():
    with pytest.raises(UnknownModel):
        load_model("wocar_dqn/8")


def test_ties_go_to_the_lowest_action():
    q = np.array([[1.0, 3.0, 3.0, 0.0, 3.0], [2.0, 2.0, 2.0, 2.0, 2.0]], dtype=np.float32)
    assert greedy(q).tolist() == [1, 0]
    assert greedy(q).tolist() == torch.from_numpy(q).argmax(dim=1).tolist()
