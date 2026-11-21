from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ActRequest(_message.Message):
    __slots__ = ("model_id", "obs", "clean_obs")
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    OBS_FIELD_NUMBER: _ClassVar[int]
    CLEAN_OBS_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    obs: _containers.RepeatedScalarFieldContainer[float]
    clean_obs: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, model_id: _Optional[str] = ..., obs: _Optional[_Iterable[float]] = ..., clean_obs: _Optional[_Iterable[float]] = ...) -> None: ...

class ActReply(_message.Message):
    __slots__ = ("action", "q", "clean_action", "clean_q", "replica_id", "handle_ms")
    ACTION_FIELD_NUMBER: _ClassVar[int]
    Q_FIELD_NUMBER: _ClassVar[int]
    CLEAN_ACTION_FIELD_NUMBER: _ClassVar[int]
    CLEAN_Q_FIELD_NUMBER: _ClassVar[int]
    REPLICA_ID_FIELD_NUMBER: _ClassVar[int]
    HANDLE_MS_FIELD_NUMBER: _ClassVar[int]
    action: int
    q: _containers.RepeatedScalarFieldContainer[float]
    clean_action: int
    clean_q: _containers.RepeatedScalarFieldContainer[float]
    replica_id: str
    handle_ms: float
    def __init__(self, action: _Optional[int] = ..., q: _Optional[_Iterable[float]] = ..., clean_action: _Optional[int] = ..., clean_q: _Optional[_Iterable[float]] = ..., replica_id: _Optional[str] = ..., handle_ms: _Optional[float] = ...) -> None: ...

class ListModelsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ModelList(_message.Message):
    __slots__ = ("models",)
    MODELS_FIELD_NUMBER: _ClassVar[int]
    models: _containers.RepeatedCompositeFieldContainer[Model]
    def __init__(self, models: _Optional[_Iterable[_Union[Model, _Mapping]]] = ...) -> None: ...

class Model(_message.Message):
    __slots__ = ("id", "group", "seed", "frame")
    ID_FIELD_NUMBER: _ClassVar[int]
    GROUP_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    FRAME_FIELD_NUMBER: _ClassVar[int]
    id: str
    group: str
    seed: int
    frame: int
    def __init__(self, id: _Optional[str] = ..., group: _Optional[str] = ..., seed: _Optional[int] = ..., frame: _Optional[int] = ...) -> None: ...
