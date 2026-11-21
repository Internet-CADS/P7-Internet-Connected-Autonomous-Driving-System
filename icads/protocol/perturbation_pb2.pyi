from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class PerturbRequest(_message.Message):
    __slots__ = ("model_id", "attack", "epsilon", "seed", "t", "obs")
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    ATTACK_FIELD_NUMBER: _ClassVar[int]
    EPSILON_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    T_FIELD_NUMBER: _ClassVar[int]
    OBS_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    attack: str
    epsilon: float
    seed: int
    t: int
    obs: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, model_id: _Optional[str] = ..., attack: _Optional[str] = ..., epsilon: _Optional[float] = ..., seed: _Optional[int] = ..., t: _Optional[int] = ..., obs: _Optional[_Iterable[float]] = ...) -> None: ...

class PerturbReply(_message.Message):
    __slots__ = ("obs", "replica_id", "handle_ms")
    OBS_FIELD_NUMBER: _ClassVar[int]
    REPLICA_ID_FIELD_NUMBER: _ClassVar[int]
    HANDLE_MS_FIELD_NUMBER: _ClassVar[int]
    obs: _containers.RepeatedScalarFieldContainer[float]
    replica_id: str
    handle_ms: float
    def __init__(self, obs: _Optional[_Iterable[float]] = ..., replica_id: _Optional[str] = ..., handle_ms: _Optional[float] = ...) -> None: ...
