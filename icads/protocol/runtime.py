"""What a result file records about where it was produced: the image and the service CPU limits.

Plain Python, shared by the experiment runner and the load generator.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

BUILD_ID_FILE = Path("/app/IMAGE_BUILD_ID")  # written once when the Docker image is built


def image_identity(build_file: Path = BUILD_ID_FILE) -> str:
    """`ICADS_IMAGE_ID` if given (e.g. the image's sha256), else the id written at image build time,
    else plain processes. A container cannot read its own image id, so the build writes one."""
    env = os.environ.get("ICADS_IMAGE_ID", "").strip()
    if env and env != "unknown":
        return env
    try:
        return f"build {build_file.read_text().strip()}"
    except OSError:
        return "none (plain processes)"


def service_cpu_limit() -> str:
    """The CPU limit of each service replica. Compose sets ICADS_SERVICE_CPUS from the same variable it
    uses for `deploy.resources.limits.cpus`."""
    cpus = os.environ.get("ICADS_SERVICE_CPUS", "").strip()
    return f"{cpus} CPU per replica" if cpus else "none (plain processes)"


def git_commit() -> str:
    env = os.environ.get("ICADS_GIT_COMMIT")
    if env and env != "unknown":
        return env
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL,
                                       cwd=Path(__file__).resolve().parent).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
