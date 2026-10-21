import importlib
import inspect
from typing import Protocol

import pytest

import hw_checker.ports

EXPECTED_PORTS = {
    "hw_checker.ports.assignments": ["AssignmentSource"],
    "hw_checker.ports.checks": ["CheckCatalog", "RequirementChecker"],
    "hw_checker.ports.clock": ["Clock"],
    "hw_checker.ports.code_host": ["CodeHost"],
    "hw_checker.ports.execution": ["CodeRunner", "DatabaseProvisioner"],
    "hw_checker.ports.git": ["GitClient"],
    "hw_checker.ports.llm": ["LLMClient"],
    "hw_checker.ports.reporting": ["ReportWriter"],
    "hw_checker.ports.roster": ["RosterReader"],
    "hw_checker.ports.storage": ["FindingRepository", "ProposalRepository", "RunRepository"],
}
ALL_PORT_NAMES = sorted(name for names in EXPECTED_PORTS.values() for name in names)


@pytest.mark.parametrize(("module_name", "names"), EXPECTED_PORTS.items())
def test_port_module_exposes_protocols(module_name: str, names: list[str]) -> None:
    module = importlib.import_module(module_name)

    for name in names:
        port = getattr(module, name)
        assert inspect.isclass(port)
        assert Protocol in port.__mro__


def test_package_reexports_every_port() -> None:
    assert sorted(hw_checker.ports.__all__) == ALL_PORT_NAMES


@pytest.mark.parametrize("name", ALL_PORT_NAMES)
def test_every_port_method_has_a_docstring(name: str) -> None:
    port = getattr(hw_checker.ports, name)
    methods = [
        member
        for member_name, member in vars(port).items()
        if inspect.isfunction(member) and not member_name.startswith("_")
    ]

    assert methods
    assert all(inspect.getdoc(method) for method in methods)
