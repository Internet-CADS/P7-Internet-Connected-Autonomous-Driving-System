"""The agent's network, rebuilt in plain PyTorch, and the checkpoint loader.

The network is the models repository's `QNetwork.features` for `highway-v0`
(50 -> 128 -> 128 -> 5, ReLU between layers, one output head). The layer order matches
`nn.Sequential` there, so the saved state dicts load with strict key matching and without
`auto_LiRPA`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn

from icads.protocol.reference import GROUPS, MODEL_IDS, NUM_ACTIONS, OBS_SIZE

HIDDEN = 128


@dataclass(frozen=True)
class ModelInfo:
    id: str
    group: str
    seed: int
    frame: int
    path: Path


class UnknownModel(KeyError):
    pass


def set_single_thread() -> None:
    """One PyTorch thread per process (reproducibility rule, D12)."""
    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:  # already set, or work already started
        pass


def build_network() -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(OBS_SIZE, HIDDEN),
        nn.ReLU(),
        nn.Linear(HIDDEN, HIDDEN),
        nn.ReLU(),
        nn.Linear(HIDDEN, NUM_ACTIONS),
    )


def checkpoint_dir() -> Path:
    env = os.environ.get("ICADS_CHECKPOINTS")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "checkpoints"


def model_info(model_id: str, root: Path | None = None) -> ModelInfo:
    if model_id not in MODEL_IDS:
        raise UnknownModel(model_id)
    group, seed = model_id.split("/")
    frame = GROUPS[group]
    root = root or checkpoint_dir()
    return ModelInfo(model_id, group, int(seed), frame, root / group / f"model_{seed}_frame_{frame}.pth")


def list_models(root: Path | None = None) -> list[ModelInfo]:
    return [model_info(m, root) for m in MODEL_IDS]


def load_model(model_id: str, root: Path | None = None) -> nn.Sequential:
    info = model_info(model_id, root)
    state = torch.load(info.path, map_location="cpu", weights_only=True)
    net = build_network()
    net.load_state_dict(state, strict=True)
    net.eval()
    for p in net.parameters():
        p.requires_grad_(False)
    return net


def load_all(root: Path | None = None) -> dict[str, nn.Sequential]:
    return {m: load_model(m, root) for m in MODEL_IDS}


def q_values(net: nn.Module, obs: np.ndarray) -> np.ndarray:
    """Q-values for a batch of observations (shape [n, 50]), as float32 [n, 5]."""
    x = torch.from_numpy(np.ascontiguousarray(obs, dtype=np.float32)).reshape(-1, OBS_SIZE)
    with torch.no_grad():
        return net(x).numpy().astype(np.float32, copy=False)


def greedy(q: np.ndarray) -> np.ndarray:
    """Argmax per row; ties go to the lowest action index."""
    return np.argmax(q, axis=-1)
