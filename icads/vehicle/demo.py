"""`icads demo`: three paced vehicles side by side on the same seed."""

from __future__ import annotations

import sys
import threading

from icads.vehicle.client import RunOutcome, RunSettings, run_vehicle

DEMO_RUNS = (
    RunSettings("vanilla_dqn/1", attack="none", epsilon=0.0, labels={"demo": "vanilla-clean"}),
    RunSettings("vanilla_dqn/1", attack="pgd", epsilon=0.01, labels={"demo": "vanilla-pgd"}),
    RunSettings("wocar_dqn/1", attack="pgd", epsilon=0.01, labels={"demo": "wocar-pgd"}),
)


def run_demo(gateway: str, seed: int = 7, pace: float = 1.0, loop: bool = False) -> int:
    failed = False
    episode = 0
    while True:
        results: list[RunOutcome | None] = [None] * len(DEMO_RUNS)

        def play(i: int, base: RunSettings) -> None:
            s = RunSettings(base.model_id, seed=seed + episode, attack=base.attack, epsilon=base.epsilon,
                            pace_hz=pace, labels=dict(base.labels))
            results[i] = run_vehicle(gateway, s, verbose=True)

        threads = [threading.Thread(target=play, args=(i, r)) for i, r in enumerate(DEMO_RUNS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for r in results:
            print(r.as_line() if r else "{}", flush=True)
            failed = failed or r is None or r.status != "finished"
        if not loop:
            return 1 if failed else 0
        episode += 1
        print(f"demo: next episode, seed {seed + episode}", file=sys.stderr, flush=True)
