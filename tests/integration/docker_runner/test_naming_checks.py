"""The committed naming requirements of _general, run for real against a fixture that breaks
four C# conventions."""

from collections.abc import Callable
from pathlib import Path

import pytest

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.adapters.docker_runner.engine import SdkDockerEngine
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.config.settings import REPO_ROOT
from hw_checker.domain import Severity, StepStatus
from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from tests.integration.docker_runner.conftest import FIXTURES, leftovers, make_request

pytestmark = pytest.mark.integration

NAMING_FIXTURE = FIXTURES / "sample_webapi_naming"


def test_naming_fixture_gives_exactly_the_four_expected_findings(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: Callable[..., Path],
    tmp_path: Path,
) -> None:
    registry = CheckRegistry(builtin_checks())
    catalog = AssignmentCatalogService(YamlAssignmentSource(REPO_ROOT / "assignments"), registry)
    general = catalog.list_assignments()[0]
    repo = sample_copy(source=NAMING_FIXTURE)

    result = docker_runner.execute(make_request(repo, tmp_path / "logs", probes=()))

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, result.logs_excerpt
    service = DeterministicCheckService(DeterministicRequirementChecker(registry))
    checked = service.evaluate(general, repo, result)
    assert checked.not_evaluated == ()
    messages = {finding.requirement_id: finding.message_en for finding in checked.findings}
    assert messages == {
        "gen-naming-properties": (
            "In C#, public properties are written in PascalCase. For example: "
            "email → Email (Models/loguser.cs, line 6). Total: 1."
        ),
        "gen-naming-locals": (
            "In C#, local variables are written in camelCase (a lowercase first letter). "
            "For example: FilePath → filePath (Controllers/User.cs, line 14). Total: 1."
        ),
        "gen-file-names": (
            "In C#, each file is named after the type it declares, in PascalCase. For example: "
            "loguser.cs → LogUser.cs (Models/loguser.cs, line 3). Total: 1."
        ),
        "gen-controller-suffix": (
            "In ASP.NET Core, controller class names end with Controller. For example: "
            "User → UserController (Controllers/User.cs, line 9). Total: 1."
        ),
    }
    assert all(finding.severity is Severity.MINOR for finding in checked.findings)
    evidence = {f.requirement_id: f.evidence for f in checked.findings}
    properties = evidence["gen-naming-properties"]
    assert properties is not None
    assert properties.quote == 'public string email { get; set; } = "";'
    assert leftovers(engine) == []
