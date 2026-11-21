"""Issue 02: each vehicle keeps one small id for the whole run, even after other vehicles are freed."""

import gc
from types import SimpleNamespace
from typing import Any

import numpy as np

from icads.vehicle.env import VehicleIds


class Car:
    def __init__(self, x):
        self.position = np.array([x, 0.0])
        self.heading = 0.0
        self.speed = 20.0


def env_with(ego, others) -> Any:
    """Just the parts of a highway-env environment that VehicleIds reads."""
    road = SimpleNamespace(vehicles=[ego, *others])
    return SimpleNamespace(unwrapped=SimpleNamespace(vehicle=ego, road=road))


def test_a_new_car_never_inherits_a_freed_cars_id():
    # Without keeping freed cars referenced, Python reuses an address within a few iterations here and
    # the new car inherits the old id.
    ids = VehicleIds()
    ego, a = Car(0), Car(10)
    seen: set[int] = set()
    for x in range(200):
        car = Car(x)
        poses = ids.poses(env_with(ego, [a, car]))
        assert [int(p[0]) for p in poses[:2]] == [0, 1]
        small = int(poses[2][0])
        assert small not in seen, "a new car got an id that was already used"
        assert int(ids.poses(env_with(ego, [a, car]))[2][0]) == small  # stable while it exists
        seen.add(small)
        del car, poses
        gc.collect()
