"""A lockstep client that owns the simulator and applies server-selected actions."""

import json
from time import perf_counter

import gymnasium as gym
import highway_env  # noqa: F401 - registers highway-v0.
from websockets.asyncio.client import ClientConnection, connect

from cads.protocol import FEATURE_NAMES, Lane, OpenRun, Outcome, Pose, RunConfig, Step

ENVIRONMENT = {
    "lanes_count": 4,
    "vehicles_count": 15,
    "duration": 60,
    "simulation_frequency": 5,
    "policy_frequency": 1,
    "collision_reward": -2.0,
    "right_lane_reward": 0.1,
    "high_speed_reward": 0.4,
    "lane_change_reward": 0.0,
    "reward_speed_range": [20, 30],
    "normalize_reward": False,
    "offroad_terminal": True,
    "action": {"type": "DiscreteMetaAction"},
    "observation": {
        "type": "Kinematics",
        "vehicles_count": 10,
        "features": list(FEATURE_NAMES),
        "absolute": False,
        "normalize": True,
        "order": "sorted",
    },
}


class RunError(RuntimeError):
    pass


class VehicleLink:
    def __init__(self, socket: ClientConnection):
        self.socket = socket
        self.run_id: str | None = None

    async def exchange(self, request, expected: str) -> dict:
        await self.socket.send(request.model_dump_json())
        response = json.loads(await self.socket.recv())
        if response["type"] == "error":
            raise RunError(f"{response['code']}: {response['message']}")
        if response["type"] != expected:
            raise RunError(f"Expected {expected} from the gateway")
        return response

    async def open(self, config: RunConfig, lanes: list[Lane]) -> str:
        response = await self.exchange(OpenRun(config=config, lanes=lanes), "opened")
        self.run_id = response["run_id"]
        return self.run_id

    async def act(self, step: Step) -> tuple[int, float]:
        start = perf_counter()
        decision = await self.exchange(step, "action")
        elapsed = (perf_counter() - start) * 1000
        action = decision.get("action")
        if decision.get("seq") != step.seq or type(action) is not int or not 0 <= action < 5:
            raise RunError("The gateway returned an invalid action")
        return action, elapsed

    async def finish_step(self, outcome: Outcome) -> bool:
        ack = await self.exchange(outcome, "committed")
        if ack.get("seq") != outcome.seq:
            raise RunError("The gateway acknowledged a different step")
        return ack["completed"]


class Simulator:
    def __init__(self, seed: int):
        self.env = gym.make("highway-v0", config=ENVIRONMENT)
        self.observation, _ = self.env.reset(seed=seed)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.env.close()

    def lanes(self) -> list[Lane]:
        network = self.env.unwrapped.road.network
        lanes = network.graph["0"]["1"]
        return [
            Lane(
                y=float(lane.start[1]),
                width=float(lane.width),
                start=float(lane.start[0]),
                end=float(lane.end[0]),
            )
            for lane in lanes
        ]

    def vehicles(self) -> list[Pose]:
        highway = self.env.unwrapped
        return [
            Pose(
                id=index,
                ego=vehicle is highway.vehicle,
                x=float(vehicle.position[0]),
                y=float(vehicle.position[1]),
                heading=float(vehicle.heading),
                speed=float(vehicle.speed),
            )
            for index, vehicle in enumerate(highway.road.vehicles)
        ]

    def step(self, seq: int) -> Step:
        return Step(seq=seq, observation=self.observation.reshape(-1).tolist(), vehicles=self.vehicles())

    def apply(self, seq: int, action: int) -> Outcome:
        self.observation, reward, terminated, truncated, _ = self.env.step(action)
        return Outcome(
            seq=seq,
            reward=float(reward),
            terminated=bool(terminated),
            truncated=bool(truncated),
            crashed=bool(self.env.unwrapped.vehicle.crashed),
            vehicles=self.vehicles(),
        )


async def run_episode(url: str, config: RunConfig) -> dict:
    with Simulator(config.seed) as simulator:
        async with connect(url, max_size=65536) as socket:
            link = VehicleLink(socket)
            run_id = await link.open(config, simulator.lanes())
            latencies = []
            reward = 0.0
            for seq in range(config.max_steps):
                action, rtt = await link.act(simulator.step(seq))
                latencies.append(rtt)
                outcome = simulator.apply(seq, action)
                outcome.client_rtt_ms = rtt
                reward += outcome.reward
                if await link.finish_step(outcome):
                    return {
                        "run_id": run_id,
                        "steps": seq + 1,
                        "crashed": outcome.crashed,
                        "total_reward": reward,
                        "rtt_ms": latencies,
                    }
    raise RunError("The run ended without a completion acknowledgement")
