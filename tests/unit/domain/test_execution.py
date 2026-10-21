from pathlib import Path

import pytest
from pydantic import ValidationError

from hw_checker.domain import (
    ExecutionRequest,
    ExecutionResult,
    FailureKind,
    ProbeResult,
    StepStatus,
)
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
        (FailureKind.PROJECT_NOT_FOUND, False),
    ],
)
def test_failure_kind_student_issue(kind: FailureKind, is_student_issue: bool) -> None:
    assert kind.is_student_issue is is_student_issue


def test_failure_kind_has_seven_values() -> None:
    assert len(FailureKind) == 7


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


FAILED, SKIPPED, SUCCEEDED = StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.SUCCEEDED


def test_prepare_status_defaults_to_succeeded() -> None:
    result = ExecutionResult(build_status=SUCCEEDED, run_status=SUCCEEDED)

    assert result.prepare_status is SUCCEEDED
    assert result.steps == (SUCCEEDED, SUCCEEDED, SUCCEEDED)


def test_failed_prepare_with_skipped_later_steps_builds() -> None:
    result = ExecutionResult(
        prepare_status=FAILED,
        build_status=SKIPPED,
        run_status=SKIPPED,
        failure_kind=FailureKind.PROJECT_NOT_FOUND,
    )

    assert result.failure_kind is FailureKind.PROJECT_NOT_FOUND


def test_failed_prepare_may_be_an_environment_error() -> None:
    result = ExecutionResult(
        prepare_status=FAILED,
        build_status=SKIPPED,
        run_status=SKIPPED,
        failure_kind=FailureKind.ENVIRONMENT_ERROR,
    )

    assert result.prepare_status is FAILED


def test_failed_prepare_without_failure_kind_is_rejected() -> None:
    with pytest.raises(ValidationError, match="needs a failure_kind"):
        ExecutionResult(prepare_status=FAILED, build_status=SKIPPED, run_status=SKIPPED)


@pytest.mark.parametrize(
    ("prepare", "build", "run"),
    [
        (FAILED, SUCCEEDED, SKIPPED),
        (FAILED, SKIPPED, SUCCEEDED),
        (FAILED, FAILED, SKIPPED),
        (SUCCEEDED, FAILED, SUCCEEDED),
        (SUCCEEDED, FAILED, FAILED),
    ],
)
def test_steps_after_a_failed_step_must_be_skipped(
    prepare: StepStatus, build: StepStatus, run: StepStatus
) -> None:
    with pytest.raises(ValidationError, match="must be skipped"):
        ExecutionResult(
            prepare_status=prepare,
            build_status=build,
            run_status=run,
            failure_kind=FailureKind.BUILD_FAILED,
        )


@pytest.mark.parametrize(("build", "run"), [(FAILED, SKIPPED), (SUCCEEDED, FAILED)])
def test_project_not_found_requires_a_failed_prepare(build: StepStatus, run: StepStatus) -> None:
    with pytest.raises(ValidationError, match="project_not_found requires a failed prepare"):
        ExecutionResult(
            build_status=build, run_status=run, failure_kind=FailureKind.PROJECT_NOT_FOUND
        )


def test_run_failure_after_successful_build_builds() -> None:
    result = ExecutionResult(
        build_status=SUCCEEDED, run_status=FAILED, failure_kind=FailureKind.STARTUP_FAILED
    )

    assert result.steps == (SUCCEEDED, SUCCEEDED, FAILED)


def execution_request(**overrides: object) -> ExecutionRequest:
    values: dict[str, object] = {
        "run_id": "run-1",
        "repo_id": "dana__api",
        "working_copy": Path("workspace/run-1/dana__api/repo"),
        "probes": (builders.probe(),),
        "logs_dir": Path("workspace/run-1/dana__api/logs"),
    }
    return ExecutionRequest.model_validate(values | overrides)


def test_execution_request_builds() -> None:
    request = execution_request(environment=(("ConnectionStrings__Default", "Server=db"),))

    assert request.environment == (("ConnectionStrings__Default", "Server=db"),)
    assert request.probes[0].requirement_id == "hw03-r2"


@pytest.mark.parametrize("field", ["run_id", "repo_id"])
def test_execution_request_rejects_padded_ids(field: str) -> None:
    with pytest.raises(ValidationError):
        execution_request(**{field: " run-1"})


def test_execution_request_rejects_blank_environment_key() -> None:
    with pytest.raises(ValidationError):
        execution_request(environment=((" ", "value"),))


def test_execution_request_rejects_duplicate_environment_keys() -> None:
    with pytest.raises(ValidationError, match="keys must be unique"):
        execution_request(environment=(("A", "1"), ("A", "2")))
