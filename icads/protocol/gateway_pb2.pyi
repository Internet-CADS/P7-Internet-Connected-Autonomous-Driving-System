from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class VehicleMsg(_message.Message):
    __slots__ = ("hello", "obs", "result")
    HELLO_FIELD_NUMBER: _ClassVar[int]
    OBS_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    hello: Hello
    obs: Obs
    result: Result
    def __init__(self, hello: _Optional[_Union[Hello, _Mapping]] = ..., obs: _Optional[_Union[Obs, _Mapping]] = ..., result: _Optional[_Union[Result, _Mapping]] = ...) -> None: ...

class GatewayMsg(_message.Message):
    __slots__ = ("welcome", "action", "summary")
    WELCOME_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    welcome: Welcome
    action: Action
    summary: Summary
    def __init__(self, welcome: _Optional[_Union[Welcome, _Mapping]] = ..., action: _Optional[_Union[Action, _Mapping]] = ..., summary: _Optional[_Union[Summary, _Mapping]] = ...) -> None: ...

class Hello(_message.Message):
    __slots__ = ("protocol_version", "scenario", "env_config_json", "seed", "max_steps", "model_id", "attack", "epsilon", "pace_hz", "obs_spec", "action_names", "road", "labels")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    SCENARIO_FIELD_NUMBER: _ClassVar[int]
    ENV_CONFIG_JSON_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    MAX_STEPS_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    ATTACK_FIELD_NUMBER: _ClassVar[int]
    EPSILON_FIELD_NUMBER: _ClassVar[int]
    PACE_HZ_FIELD_NUMBER: _ClassVar[int]
    OBS_SPEC_FIELD_NUMBER: _ClassVar[int]
    ACTION_NAMES_FIELD_NUMBER: _ClassVar[int]
    ROAD_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    protocol_version: int
    scenario: str
    env_config_json: str
    seed: int
    max_steps: int
    model_id: str
    attack: str
    epsilon: float
    pace_hz: float
    obs_spec: ObsSpec
    action_names: _containers.RepeatedScalarFieldContainer[str]
    road: Road
    labels: _containers.ScalarMap[str, str]
    def __init__(self, protocol_version: _Optional[int] = ..., scenario: _Optional[str] = ..., env_config_json: _Optional[str] = ..., seed: _Optional[int] = ..., max_steps: _Optional[int] = ..., model_id: _Optional[str] = ..., attack: _Optional[str] = ..., epsilon: _Optional[float] = ..., pace_hz: _Optional[float] = ..., obs_spec: _Optional[_Union[ObsSpec, _Mapping]] = ..., action_names: _Optional[_Iterable[str]] = ..., road: _Optional[_Union[Road, _Mapping]] = ..., labels: _Optional[_Mapping[str, str]] = ...) -> None: ...

class ObsSpec(_message.Message):
    __slots__ = ("features", "vehicles", "absolute", "ranges")
    class RangesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: Range
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[Range, _Mapping]] = ...) -> None: ...
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    VEHICLES_FIELD_NUMBER: _ClassVar[int]
    ABSOLUTE_FIELD_NUMBER: _ClassVar[int]
    RANGES_FIELD_NUMBER: _ClassVar[int]
    features: _containers.RepeatedScalarFieldContainer[str]
    vehicles: int
    absolute: bool
    ranges: _containers.MessageMap[str, Range]
    def __init__(self, features: _Optional[_Iterable[str]] = ..., vehicles: _Optional[int] = ..., absolute: _Optional[bool] = ..., ranges: _Optional[_Mapping[str, Range]] = ...) -> None: ...

class Range(_message.Message):
    __slots__ = ("low", "high")
    LOW_FIELD_NUMBER: _ClassVar[int]
    HIGH_FIELD_NUMBER: _ClassVar[int]
    low: float
    high: float
    def __init__(self, low: _Optional[float] = ..., high: _Optional[float] = ...) -> None: ...

class Road(_message.Message):
    __slots__ = ("lanes",)
    LANES_FIELD_NUMBER: _ClassVar[int]
    lanes: _containers.RepeatedCompositeFieldContainer[Lane]
    def __init__(self, lanes: _Optional[_Iterable[_Union[Lane, _Mapping]]] = ...) -> None: ...

class Lane(_message.Message):
    __slots__ = ("polyline", "width")
    POLYLINE_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    polyline: _containers.RepeatedCompositeFieldContainer[Point]
    width: float
    def __init__(self, polyline: _Optional[_Iterable[_Union[Point, _Mapping]]] = ..., width: _Optional[float] = ...) -> None: ...

class Point(_message.Message):
    __slots__ = ("x", "y")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ...) -> None: ...

class Pose(_message.Message):
    __slots__ = ("id", "x", "y", "heading", "speed")
    ID_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    HEADING_FIELD_NUMBER: _ClassVar[int]
    SPEED_FIELD_NUMBER: _ClassVar[int]
    id: int
    x: float
    y: float
    heading: float
    speed: float
    def __init__(self, id: _Optional[int] = ..., x: _Optional[float] = ..., y: _Optional[float] = ..., heading: _Optional[float] = ..., speed: _Optional[float] = ...) -> None: ...

class Welcome(_message.Message):
    __slots__ = ("run_id",)
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    def __init__(self, run_id: _Optional[str] = ...) -> None: ...

class Obs(_message.Message):
    __slots__ = ("t", "obs", "poses")
    T_FIELD_NUMBER: _ClassVar[int]
    OBS_FIELD_NUMBER: _ClassVar[int]
    POSES_FIELD_NUMBER: _ClassVar[int]
    t: int
    obs: _containers.RepeatedScalarFieldContainer[float]
    poses: _containers.RepeatedCompositeFieldContainer[Pose]
    def __init__(self, t: _Optional[int] = ..., obs: _Optional[_Iterable[float]] = ..., poses: _Optional[_Iterable[_Union[Pose, _Mapping]]] = ...) -> None: ...

class Action(_message.Message):
    __slots__ = ("t", "action")
    T_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    t: int
    action: int
    def __init__(self, t: _Optional[int] = ..., action: _Optional[int] = ...) -> None: ...

class Result(_message.Message):
    __slots__ = ("t", "reward", "terminated", "truncated", "crashed", "rtt_ms", "final_poses")
    T_FIELD_NUMBER: _ClassVar[int]
    REWARD_FIELD_NUMBER: _ClassVar[int]
    TERMINATED_FIELD_NUMBER: _ClassVar[int]
    TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    CRASHED_FIELD_NUMBER: _ClassVar[int]
    RTT_MS_FIELD_NUMBER: _ClassVar[int]
    FINAL_POSES_FIELD_NUMBER: _ClassVar[int]
    t: int
    reward: float
    terminated: bool
    truncated: bool
    crashed: bool
    rtt_ms: float
    final_poses: _containers.RepeatedCompositeFieldContainer[Pose]
    def __init__(self, t: _Optional[int] = ..., reward: _Optional[float] = ..., terminated: _Optional[bool] = ..., truncated: _Optional[bool] = ..., crashed: _Optional[bool] = ..., rtt_ms: _Optional[float] = ..., final_poses: _Optional[_Iterable[_Union[Pose, _Mapping]]] = ...) -> None: ...

class Summary(_message.Message):
    __slots__ = ("run_id", "status", "reason", "message", "steps", "total_reward", "crashed")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    STEPS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_REWARD_FIELD_NUMBER: _ClassVar[int]
    CRASHED_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    status: str
    reason: str
    message: str
    steps: int
    total_reward: float
    crashed: bool
    def __init__(self, run_id: _Optional[str] = ..., status: _Optional[str] = ..., reason: _Optional[str] = ..., message: _Optional[str] = ..., steps: _Optional[int] = ..., total_reward: _Optional[float] = ..., crashed: _Optional[bool] = ...) -> None: ...
