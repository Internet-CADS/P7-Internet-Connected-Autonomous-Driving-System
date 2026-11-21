"""Load-test profiles (docs/ARCHITECTURE.md, section 15).

A profile file is JSON:

    {
      "name": "capacity",
      "recording": "recording.jsonl",     # an exported step log (made on first use if missing)
      "model": "vanilla_dqn/1",           # optional: the model to replay with (default: the recording's)
      "attack": ["none", "pgd"],          # one value or a list (one phase per value)
      "epsilon": 0.01,
      "paceHz": 1,                        # 0 = unpaced
      "viewersPerRun": 5,
      "durationS": 60,                    # how long new runs keep being started
      "runs": [10, 50],                   # concurrent replay vehicles; "2x" = twice the gateway's limit
      "note": "free text, e.g. how many replicas the stack was started with"
    }

Every combination of `attack` and `runs` is one phase, played one after another.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Phase:
    runs: int | str
    attack: str
    epsilon: float
    pace_hz: float
    viewers_per_run: int
    duration_s: float
    model: str | None

    def label(self) -> str:
        return f"runs={self.runs} attack={self.attack} eps={self.epsilon:g} pace={self.pace_hz:g}"


@dataclass(frozen=True)
class Profile:
    name: str
    raw: dict[str, Any]
    recording_path: Path
    load_sample_s: float = 1.0
    record_model: str = "vanilla_dqn/1"
    record_seed: int = 7

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        path = Path(path)
        raw = json.loads(path.read_text())
        rec = Path(raw.get("recording", "recording.jsonl"))
        if not rec.is_absolute():
            rec = path.parent / rec
        return cls(name=str(raw["name"]), raw=raw, recording_path=rec,
                   load_sample_s=float(raw.get("loadSampleS", 1.0)),
                   record_model=str(raw.get("recordModel", "vanilla_dqn/1")),
                   record_seed=int(raw.get("recordSeed", 7)))

    def phases(self) -> list[Phase]:
        r = self.raw
        attacks = r.get("attack", "none")
        attacks = attacks if isinstance(attacks, list) else [attacks]
        runs = r.get("runs", 1)
        runs = runs if isinstance(runs, list) else [runs]
        out = []
        for a in attacks:
            for n in runs:
                out.append(Phase(runs=n if isinstance(n, str) else int(n), attack=str(a),
                                 epsilon=float(r.get("epsilon", 0.0)) if a != "none" else 0.0,
                                 pace_hz=float(r.get("paceHz", 0.0)), viewers_per_run=int(r.get("viewersPerRun", 0)),
                                 duration_s=float(r.get("durationS", 30.0)), model=r.get("model")))
        return out
