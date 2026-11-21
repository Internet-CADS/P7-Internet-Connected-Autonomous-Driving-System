"""Runs the page's JavaScript unit tests with Node, when Node is installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_overlay_javascript():
    subprocess.run(["node", "--test", "tests/web/*.test.mjs"], check=True, cwd=ROOT)
