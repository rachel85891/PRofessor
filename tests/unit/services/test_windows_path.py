"""A path written with Windows folder separators does not fail the student (`windows_path`).

The run logs are synthetic; they read like an ASP.NET Core app's unhandled-exception output.
"""

from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.windows_paths import windows_paths_in_log
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    ExecutionResult,
    HttpMethod,
    HttpProbe,
    NotEvaluated,
    NotEvaluatedReason,
    ProbeCapture,
    ProbeResult,
    Requirement,
    RequirementKind,
    RouteResolution,
    Severity,
    StepStatus,
)
from hw_checker.services.deterministic_checks import DeterministicCheckService

WINDOWS_LOG = (
    "fail: Microsoft.AspNetCore.Server.Kestrel[13]\n"
    "      An unhandled exception was thrown by the application.\n"
    "      System.IO.DirectoryNotFoundException: Could not find a part of the path "
    "'/app/..\\x\\y.txt'.\n"
    "         at Microsoft.Win32.SafeHandles.SafeFileHandle.Open(String fullPath)\n"
)
LINUX_LOG = WINDOWS_LOG.replace("..\\x\\y.txt", "../x/y.txt")
WINDOWS_PATH = "/app/..\\x\\y.txt"


def probe(
    requirement_id: str, method: HttpMethod, path: str, status: int, **extra: object
) -> HttpProbe:
    return HttpProbe.model_validate(
        {
            "requirement_id": requirement_id,
            "assignment_id": "hw05",
            "method": method,
            "path": path,
            "expected_status": status,
            **extra,
        }
    )


CREATE = probe(
    "hw05-create",
    HttpMethod.POST,
    "/api/items",
    201,
    body='{"name":"x"}',
    capture=ProbeCapture(name="itemId", source="body:id"),
)
READ_BACK = probe("hw05-read-back", HttpMethod.GET, "/api/items/{itemId}", 200)
LIST = probe("hw05-list", HttpMethod.GET, "/api/items", 200)


def http(requirement_id: str, *probes: HttpProbe, depends_on: tuple[str, ...] = ()) -> Requirement:
    return Requirement(
        id=requirement_id,
        title=requirement_id,
        severity=Severity.MAJOR,
        kind=RequirementKind.HTTP,
        http_probes=probes,
        depends_on=depends_on,
    )


ASSIGNMENT = Assignment(
    id="hw05",
    order=5,
    title="Items",
    requirements=(
        http("hw05-create", CREATE),
        http("hw05-read-back", READ_BACK),
        http("hw05-list", LIST, depends_on=("hw05-create",)),
    ),
)


def evaluate(
    log: str, list_status: int = 500, assignment: Assignment = ASSIGNMENT
) -> AssignmentResult:
    results = (
        ProbeResult(probe=CREATE, actual_status=500, passed=False, sent_path="/api/items"),
        ProbeResult(
            probe=READ_BACK, passed=False, route_resolution=RouteResolution.DEPENDENCY_MISSING
        ),
        ProbeResult(
            probe=LIST,
            actual_status=list_status,
            passed=list_status == 200,
            sent_path="/api/items",
        ),
    )
    execution = ExecutionResult(
        build_status=StepStatus.SUCCEEDED,
        run_status=StepStatus.SUCCEEDED,
        probe_results=results,
        windows_paths=windows_paths_in_log(log),
    )
    checker = DeterministicRequirementChecker(CheckRegistry(builtin_checks()))
    return DeterministicCheckService(checker).evaluate(assignment, Path("repo"), execution)


def test_the_run_log_names_only_paths_with_a_windows_separator() -> None:
    other = "System.IO.FileNotFoundException: Could not find file '/app/..\\repository\\Users.txt'."

    assert windows_paths_in_log(WINDOWS_LOG) == (WINDOWS_PATH,)
    assert windows_paths_in_log(WINDOWS_LOG + other + "\n" + WINDOWS_LOG) == (
        WINDOWS_PATH,
        "/app/..\\repository\\Users.txt",
    )
    assert windows_paths_in_log(LINUX_LOG) == ()
    assert windows_paths_in_log("System.Exception: bad value 'a\\b'") == ()


def test_a_windows_path_makes_the_probe_and_its_dependents_windows_path() -> None:
    result = evaluate(WINDOWS_LOG)

    assert result.findings == ()
    assert result.not_evaluated == (
        NotEvaluated(
            requirement_id="hw05-create",
            reason=NotEvaluatedReason.WINDOWS_PATH,
            detail=WINDOWS_PATH,
        ),
        # Not sent: it needed the id the failed POST should have returned.
        NotEvaluated(
            requirement_id="hw05-read-back",
            reason=NotEvaluatedReason.WINDOWS_PATH,
            detail=WINDOWS_PATH,
        ),
        NotEvaluated(
            requirement_id="hw05-list",
            reason=NotEvaluatedReason.WINDOWS_PATH,
            detail=WINDOWS_PATH,
        ),
    )


def test_a_requirement_that_depends_on_it_but_passes_still_passes() -> None:
    result = evaluate(WINDOWS_LOG, list_status=200)

    assert "hw05-list" not in {entry.requirement_id for entry in result.not_evaluated}
    assert result.findings == ()


def test_the_same_exception_for_a_path_with_slashes_stays_a_finding() -> None:
    result = evaluate(LINUX_LOG)

    assert {finding.requirement_id for finding in result.findings} == {
        "hw05-create",
        "hw05-list",
    }
    assert result.not_evaluated == (
        NotEvaluated(requirement_id="hw05-read-back", reason=NotEvaluatedReason.DEPENDENCY_FAILED),
    )


@pytest.mark.parametrize("status", [400, 404])
def test_a_client_error_of_a_dependent_follows_its_dependency(status: int) -> None:
    result = evaluate(WINDOWS_LOG, list_status=status)

    assert {entry.reason for entry in result.not_evaluated} == {NotEvaluatedReason.WINDOWS_PATH}


def test_a_client_error_of_its_own_stays_a_finding_next_to_a_windows_path() -> None:
    independent = ASSIGNMENT.model_copy(
        update={
            "requirements": (
                *ASSIGNMENT.requirements[:2],
                http("hw05-list", LIST),
            )
        }
    )

    result = evaluate(WINDOWS_LOG, list_status=400, assignment=independent)

    assert [finding.requirement_id for finding in result.findings] == ["hw05-list"]
