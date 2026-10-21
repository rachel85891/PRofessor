from pathlib import Path

import pytest
from pydantic import ValidationError

from hw_checker.domain import ExecutionResult, FailureKind, ProbeResult, StepStatus
from hw_checker.domain.execution import MAX_LOGS_EXCERPT_CHARS, MAX_PROBE_ERROR_CHARS
from tests.unit.domain import builders


@pytest.mark.parametrize(
    ("kind", "is_student_issue"),
    [
        (FailureKind.BUILD_FAILED, True),
        (FailureKind.STARTUP_FAILED, True),
        (FailureKind.DB_UNREACHABLE, False),
        (FailureKind.HARDCODED_CONNECTION_UNRESOLVED, False),
        (FailureKind.TIMEOUT, False),
        (FailureKind.ENVIRONMENT_ERROR, False),
    ],
)
def test_failure_kind_student_issue(kind: FailureKind, is_student_issue: bool) -> None:
    assert kind.is_student_issue is is_student_issue


def test_failure_kind_has_six_values() -> None:
    assert len(FailureKind) == 6


def test_probe_result_passed_with_allowed_status() -> None:
    result = ProbeResult(probe=builders.probe(), actual_status=200, passed=True)

    assert result.passed


def test_probe_result_failed_without_response() -> None:
    result = ProbeResult(probe=builders.probe(), passed=False, error="Connection refused")

    assert result.actual_status is None


def test_probe_result_rejects_passed_with_disallowed_status() -> None:
    with pytest.raises(ValidationError, match="passed must be true exactly"):
        ProbeResult(probe=builders.probe(), actual_status=500, passed=True)


def test_probe_result_rejects_failed_with_allowed_status() -> None:
    with pytest.raises(ValidationError, match="passed must be true exactly"):
        ProbeResult(probe=builders.probe(), actual_status=200, passed=False)


def test_probe_result_rejects_long_error() -> None:
    with pytest.raises(ValidationError):
        ProbeResult(probe=builders.probe(), passed=False, error="x" * (MAX_PROBE_ERROR_CHARS + 1))


def test_successful_execution_builds() -> None:
    result = ExecutionResult(
        build_status=StepStatus.SUCCEEDED,
        run_status=StepStatus.SUCCEEDED,
        logs_path=Path("workspace/run-1/dana-api/build.log"),
        probe_results=(ProbeResult(probe=builders.probe(), actual_status=200, passed=True),),
    )

    assert result.failure_kind is None


def test_failed_build_with_failure_kind_builds() -> None:
    result = ExecutionResult(
        build_status=StepStatus.FAILED,
        run_status=StepStatus.SKIPPED,
        failure_kind=FailureKind.BUILD_FAILED,
    )

    assert result.failure_kind is FailureKind.BUILD_FAILED


def test_failed_step_without_failure_kind_is_rejected() -> None:
    with pytest.raises(ValidationError, match="needs a failure_kind"):
        ExecutionResult(build_status=StepStatus.SUCCEEDED, run_status=StepStatus.FAILED)


def test_failure_kind_without_failed_step_is_rejected() -> None:
    with pytest.raises(ValidationError, match="only allowed when a step failed"):
        ExecutionResult(
            build_status=StepStatus.SUCCEEDED,
            run_status=StepStatus.SUCCEEDED,
            failure_kind=FailureKind.TIMEOUT,
        )


def test_probe_results_without_successful_run_are_rejected() -> None:
    with pytest.raises(ValidationError, match="require a successful run"):
        ExecutionResult(
            build_status=StepStatus.FAILED,
            run_status=StepStatus.SKIPPED,
            failure_kind=FailureKind.BUILD_FAILED,
            probe_results=(ProbeResult(probe=builders.probe(), passed=False),),
        )


def test_logs_excerpt_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult(
            build_status=StepStatus.SUCCEEDED,
            run_status=StepStatus.SUCCEEDED,
            logs_excerpt="x" * (MAX_LOGS_EXCERPT_CHARS + 1),
        )


def test_probe_result_is_traceable_to_requirement_and_assignment() -> None:
    result = ProbeResult(probe=builders.probe(), actual_status=200, passed=True)

    assert (result.requirement_id, result.assignment_id) == ("hw03-r2", "hw03")
