"""An app that cannot reach its database does not fail the student (`database_unavailable`).

The run logs are synthetic; they read like an ASP.NET Core app's output with SQL Server.
"""

from pathlib import Path

from hw_checker.adapters.docker_runner.database_errors import unreachable_databases_in_log
from hw_checker.adapters.docker_runner.windows_paths import windows_paths_in_log
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import (
    AssignmentResult,
    ExecutionResult,
    FailureKind,
    NotEvaluated,
    NotEvaluatedReason,
    ProbeResult,
    RouteResolution,
    StepStatus,
)
from hw_checker.services.deterministic_checks import DeterministicCheckService
from tests.unit.services.test_windows_path import ASSIGNMENT, CREATE, LIST, READ_BACK, WINDOWS_LOG

DATABASE_LOG = (
    "fail: Microsoft.EntityFrameworkCore.Database.Connection[20004]\n"
    "      An error occurred using the connection to database 'ItemsDb' on server "
    "'TEST-PC\\SQLEXPRESS'.\n"
    "fail: Microsoft.AspNetCore.Server.Kestrel[13]\n"
    "      An unhandled exception was thrown by the application.\n"
    "      Microsoft.Data.SqlClient.SqlException (0x80131904): A network-related or "
    "instance-specific error occurred while establishing a connection to SQL Server.\n"
)
DETAIL = "database 'ItemsDb', SQL Server"


def checker() -> DeterministicRequirementChecker:
    return DeterministicRequirementChecker(CheckRegistry(builtin_checks()))


def evaluate(log: str, list_status: int = 500, windows_log: str = "") -> AssignmentResult:
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
        unreachable_databases=unreachable_databases_in_log(log),
        windows_paths=windows_paths_in_log(windows_log),
    )
    return DeterministicCheckService(checker()).evaluate(ASSIGNMENT, Path("repo"), execution)


def test_an_unreachable_database_makes_the_probe_and_its_dependents_database_unavailable() -> None:
    result = evaluate(DATABASE_LOG)

    assert result.findings == ()
    assert result.not_evaluated == (
        NotEvaluated(
            requirement_id="hw05-create",
            reason=NotEvaluatedReason.DATABASE_UNAVAILABLE,
            detail=DETAIL,
        ),
        # Not sent: it needed the id the failed POST should have returned.
        NotEvaluated(
            requirement_id="hw05-read-back",
            reason=NotEvaluatedReason.DATABASE_UNAVAILABLE,
            detail=DETAIL,
        ),
        NotEvaluated(
            requirement_id="hw05-list",
            reason=NotEvaluatedReason.DATABASE_UNAVAILABLE,
            detail=DETAIL,
        ),
    )


def test_a_dependent_s_client_error_follows_its_dependency() -> None:
    result = evaluate(DATABASE_LOG, list_status=404)

    assert result.findings == ()
    assert {entry.reason for entry in result.not_evaluated} == {
        NotEvaluatedReason.DATABASE_UNAVAILABLE
    }


def test_a_server_error_without_a_database_failure_in_the_log_stays_a_finding() -> None:
    result = evaluate("info: started\n")

    assert {finding.requirement_id for finding in result.findings} == {
        "hw05-create",
        "hw05-list",
    }
    assert result.not_evaluated == (
        NotEvaluated(requirement_id="hw05-read-back", reason=NotEvaluatedReason.DEPENDENCY_FAILED),
    )


def test_a_windows_path_comes_before_an_unreachable_database() -> None:
    result = evaluate(DATABASE_LOG, windows_log=WINDOWS_LOG)

    assert {entry.reason for entry in result.not_evaluated} == {NotEvaluatedReason.WINDOWS_PATH}


def test_an_app_that_did_not_start_for_its_database_is_not_held_against_the_student() -> None:
    execution = ExecutionResult(
        build_status=StepStatus.SUCCEEDED,
        run_status=StepStatus.FAILED,
        failure_kind=FailureKind.STARTUP_FAILED,
        unreachable_databases=unreachable_databases_in_log(DATABASE_LOG),
    )
    assignment = ASSIGNMENT.model_copy(update={"requirements": ASSIGNMENT.requirements[:1]})

    result = DeterministicCheckService(checker()).evaluate(assignment, Path("repo"), execution)

    assert result.not_evaluated == (
        NotEvaluated(
            requirement_id="hw05-create",
            reason=NotEvaluatedReason.DATABASE_UNAVAILABLE,
            detail=DETAIL,
        ),
    )
