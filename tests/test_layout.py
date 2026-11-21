"""docs/ARCHITECTURE.md, section 17: shared code lives in protocol, policy and attacks; components do not
import each other."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "icads"
COMPONENTS = ("gateway", "inference", "perturbation", "vehicle", "experiments", "loadgen")


def imported_components(component: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in (PACKAGE / component).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                parts = name.split(".")
                if len(parts) > 1 and parts[0] == "icads" and parts[1] in COMPONENTS and parts[1] != component:
                    found.setdefault(path.name, []).append(name)
    return found


@pytest.mark.parametrize("component", COMPONENTS)
def test_components_do_not_import_each_other(component):
    assert imported_components(component) == {}
