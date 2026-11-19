"""Versioned JSON message shapes shared by the services and browser feed."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, TypeAdapter

VERSION = 1
ACTION_NAMES = ("LANE_LEFT", "IDLE", "LANE_RIGHT", "FASTER", "SLOWER")
FEATURE_NAMES = ("presence", "x", "y", "vx", "vy")
FRAMES = {"vanilla_dqn": 1500000, "sa_dqn": 1600000, "wocar_dqn": 1450000}
EPSILONS = (0, 0.0025, 0.005, 0.0075, 0.01)
METHODS = ("random_linf", "pgd", "minbest", "worst_q")

Group = Literal["vanilla_dqn", "sa_dqn", "wocar_dqn"]
Attack = Literal["random_linf", "pgd", "minbest", "worst_q"]
Budget = Literal[0, 0.0025, 0.005, 0.0075, 0.01]
EpisodeSeed = Annotated[int, Field(ge=0, le=2**32 - 1)]
Sequence = Annotated[int, Field(ge=0, lt=60)]
Action = Annotated[int, Field(ge=0, lt=len(ACTION_NAMES))]
Vector = Annotated[list[FiniteFloat], Field(min_length=50, max_length=50)]
Scores = Annotated[list[FiniteFloat], Field(min_length=5, max_length=5)]


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ModelId(Message):
    group: Group
    seed: Annotated[int, Field(ge=1, le=7)]

    @property
    def frame(self) -> int:
        return FRAMES[self.group]


class Lane(Message):
    y: FiniteFloat
    width: Annotated[FiniteFloat, Field(gt=0)]
    start: FiniteFloat
    end: FiniteFloat


class Pose(Message):
    id: Annotated[int, Field(ge=0)]
    ego: bool
    x: FiniteFloat
    y: FiniteFloat
    heading: FiniteFloat
    speed: FiniteFloat


Poses = Annotated[list[Pose], Field(min_length=1, max_length=16)]


class RunConfig(Message):
    seed: EpisodeSeed
    model: ModelId
    method: Attack
    epsilon: Budget
    max_steps: Annotated[int, Field(ge=1, le=60)] = 60


class OpenRun(Message):
    type: Literal["open"] = "open"
    version: Literal[1] = VERSION
    config: RunConfig
    lanes: Annotated[list[Lane], Field(min_length=4, max_length=4)]


class Step(Message):
    type: Literal["step"] = "step"
    seq: Sequence
    observation: Vector
    vehicles: Poses


class Outcome(Message):
    type: Literal["outcome"] = "outcome"
    seq: Sequence
    reward: FiniteFloat
    terminated: bool
    truncated: bool
    crashed: bool
    vehicles: Poses
    client_rtt_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None


class FailRun(Message):
    type: Literal["fail"] = "fail"
    reason: Annotated[str, Field(min_length=1, max_length=250)]


VehicleMessage = TypeAdapter(Annotated[OpenRun | Step | Outcome | FailRun, Field(discriminator="type")])


class Infer(Message):
    type: Literal["infer"] = "infer"
    version: Literal[1] = VERSION
    run_id: UUID
    seq: Sequence
    model: ModelId
    observation: Vector
    clean_observation: Vector | None = None


class InferenceResult(Message):
    type: Literal["inference"] = "inference"
    run_id: UUID
    seq: Sequence
    action: Action
    scores: Scores
    clean_action: Action
    clean_scores: Scores
    inference_ms: Annotated[FiniteFloat, Field(ge=0)]


def error(code: str, message: str) -> dict:
    return {"type": "error", "code": code, "message": message}
