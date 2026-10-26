"""The committed hw01 requirements, run for real against correct sample solutions.

Each fixture is a correct solution, so every requirement must pass: no findings and nothing
left "not evaluated". A failure here means the requirements or the runner reject a correct
project.
"""

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
from hw_checker.domain import ExecutionResult, StepStatus
from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from tests.integration.docker_runner.conftest import (
    CACHE_VOLUME,
    FIXTURE,
    FIXTURES,
    leftovers,
    make_request,
)

CORRECT_SOLUTIONS = {
    "sample_webapi": FIXTURE,
    # Its ids are called userId, so the capture body:id has to find them leniently.
    "sample_webapi_user_id": FIXTURES / "sample_webapi_user_id",
}

pytestmark = pytest.mark.integration


def describe(result: ExecutionResult) -> str:
    lines = [
        f"{r.requirement_id} {r.probe.method.value} {r.sent_path or r.probe.path}: "
        f"{r.actual_status} {r.route_resolution.value} {r.error} {r.response_excerpt[:120]}"
        for r in result.probe_results
    ]
    return "\n".join([*lines, result.logs_excerpt])


@pytest.mark.parametrize("fixture", sorted(CORRECT_SOLUTIONS))
def test_hw01_requirements_give_no_findings_on_a_correct_solution(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: Callable[..., Path],
    tmp_path: Path,
    fixture: str,
) -> None:
    registry = CheckRegistry(builtin_checks())
    catalog = AssignmentCatalogService(YamlAssignmentSource(REPO_ROOT / "assignments"), registry)
    assignments = catalog.select_for_round("hw01")
    probes = tuple(
        probe
        for assignment in assignments
        for requirement in assignment.requirements
        for probe in requirement.http_probes
    )
    repo = sample_copy(source=CORRECT_SOLUTIONS[fixture])

    result = docker_runner.execute(make_request(repo, tmp_path / "logs", probes=probes))

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, describe(result)
    assert len(result.probe_results) == 9
    service = DeterministicCheckService(DeterministicRequirementChecker(registry))
    for assignment in assignments:
        checked = service.evaluate(assignment, repo, result)
        assert checked.findings == (), describe(result)
        assert checked.not_evaluated == (), describe(result)
    assert leftovers(engine) == []
    assert engine.volume_exists(CACHE_VOLUME)
