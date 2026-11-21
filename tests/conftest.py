from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def reference() -> dict:
    return json.loads((FIXTURES / "reference.json").read_text())


@pytest.fixture(scope="session")
def reference_obs(reference) -> np.ndarray:
    return np.asarray(reference["observations"], dtype=np.float32)
