"""The committed hw01 requirements, run for real against sample solutions.

Each correct fixture must pass every requirement: no findings and nothing left "not
evaluated". A failure there means the requirements or the runner reject a correct
project. The variants below break one thing in a copy of the main fixture and check that
exactly the matching requirement reports it.
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
from hw_checker.domain import Assignment, ExecutionResult, Finding, NotEvaluated, StepStatus
from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from tests.integration.docker_runner.conftest import (
    CACHE_VOLUME,
    FIXTURE,
    FIXTURES,
    PROJECT,
    leftovers,
    make_request,
    replace_in,
)

pytestmark = pytest.mark.integration

CORRECT_SOLUTIONS = {
    "sample_webapi": FIXTURE,
    # Its ids are called userId, so the capture body:id has to find them leniently.
    "sample_webapi_user_id": FIXTURES / "sample_webapi_user_id",
}
CONTROLLER = PROJECT / "Controllers" / "UsersController.cs"


def describe(result: ExecutionResult) -> str:
    lines = [
        f"{r.requirement_id} {r.probe.method.value} {r.sent_path or r.probe.path}: "
        f"{r.actual_status} {r.route_resolution.value} {r.response_shape} {r.missing_values} "
        f"{r.error} {r.response_excerpt[:120]}"
        for r in result.probe_results
    ]
    return "\n".join([*lines, result.logs_excerpt])


def run_round(
    docker_runner: DockerCodeRunner, repo: Path, logs: Path
) -> tuple[ExecutionResult, list[Finding], list[NotEvaluated]]:
    """Run every probe of the hw01 round, then evaluate _general and hw01 for real."""
    registry = CheckRegistry(builtin_checks())
    catalog = AssignmentCatalogService(YamlAssignmentSource(REPO_ROOT / "assignments"), registry)
    assignments: tuple[Assignment, ...] = catalog.select_for_round("hw01")
    probes = tuple(
        probe
        for assignment in assignments
        for requirement in assignment.requirements
        for probe in requirement.http_probes
    )
    result = docker_runner.execute(make_request(repo, logs, probes=probes))
    service = DeterministicCheckService(DeterministicRequirementChecker(registry))
    findings: list[Finding] = []
    not_evaluated: list[NotEvaluated] = []
    for assignment in assignments:
        checked = service.evaluate(assignment, repo, result)
        findings += checked.findings
        not_evaluated += checked.not_evaluated
    return result, findings, not_evaluated


@pytest.mark.parametrize("fixture", sorted(CORRECT_SOLUTIONS))
def test_hw01_requirements_give_no_findings_on_a_correct_solution(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: Callable[..., Path],
    tmp_path: Path,
    fixture: str,
) -> None:
    repo = sample_copy(source=CORRECT_SOLUTIONS[fixture])

    result, findings, not_evaluated = run_round(docker_runner, repo, tmp_path / "logs")

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, describe(result)
    assert len(result.probe_results) == 7
    assert (findings, not_evaluated) == ([], []), describe(result)
    assert leftovers(engine) == []
    assert engine.volume_exists(CACHE_VOLUME)


def read_back_without_email(repo: Path) -> None:
    replace_in(
        repo / CONTROLLER,
        "                return NotFound();\n            return Ok(user);",
        "                return NotFound();\n            return Ok(new { user.Id });",
    )


def wrong_login_401(repo: Path) -> None:
    replace_in(
        repo / CONTROLLER,
        "                return NoContent();\n            return Ok(user);",
        "                return Unauthorized();\n            return Ok(user);",
    )


@pytest.mark.parametrize(
    ("break_it", "requirement_id", "message"),
    [
        (
            read_back_without_email,
            "hw01-read-back",
            'GET /api/users/{user_id}: the response does not contain "probe.user@example.com"',
        ),
        (
            wrong_login_401,
            "hw01-login-wrong-204",
            "POST /api/users/login: expected 204, got 401",
        ),
    ],
    ids=["read-back-without-email", "wrong-login-401"],
)
def test_one_broken_behaviour_gives_exactly_its_finding(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: Callable[..., Path],
    tmp_path: Path,
    break_it: Callable[[Path], None],
    requirement_id: str,
    message: str,
) -> None:
    repo = sample_copy(edit=break_it)

    result, findings, not_evaluated = run_round(docker_runner, repo, tmp_path / "logs")

    assert not_evaluated == [], describe(result)
    assert [finding.requirement_id for finding in findings] == [requirement_id], describe(result)
    assert findings[0].message_en.endswith(message)
    assert leftovers(engine) == []
