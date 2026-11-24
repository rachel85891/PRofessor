"""No check module imports a private name from another module."""

import ast
from pathlib import Path

import pytest

import hw_checker.checks.deterministic as deterministic

_FOLDER = Path(deterministic.__file__).parent
_MODULES = ("layer_checks", "pattern_checks", "layer_support")


def _private_imports(module: str) -> list[str]:
    tree = ast.parse((_FOLDER / f"{module}.py").read_text(encoding="utf-8"))
    return [
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
        if alias.name.startswith("_") and node.module.startswith("hw_checker.")
    ]


@pytest.mark.parametrize("module", _MODULES)
def test_a_check_module_imports_no_private_name_from_another(module: str) -> None:
    assert _private_imports(module) == []
