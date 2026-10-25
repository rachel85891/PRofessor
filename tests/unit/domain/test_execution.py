from pathlib import Path

import pytest
from pydantic import ValidationError

from hw_checker.domain import (
    BodyAdaptation,
    BodyFieldNote,
    BodyFieldOutcome,
    CaptureFailure,
    CaptureResult,
    ExecutionRequest,
    ExecutionResult,
    FailureKind,
    PathFixKind,
    PathNotRewrittenReason,
    PathRewrite,
    ProbeResult,
    RouteResolution,
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


def path_rewrite(rewritten: str | None) -> PathRewrite:
    return PathRewrite(
        file_path="WebApi/Store.cs",
        line=3,
        kind=PathFixKind.ABSOLUTE,
        original="C:\\data\\users.txt",
        rewritten=rewritten,
        not_rewritten_reason=None if rewritten else PathNotRewrittenReason.NO_MATCH,
    )


def test_path_rewrite_is_either_rewritten_or_has_a_reason() -> None:
    with pytest.raises(ValidationError, match="either rewritten"):
        PathRewrite(file_path="Store.cs", line=1, kind=PathFixKind.CASE, original="users.txt")
    with pytest.raises(ValidationError, match="either rewritten"):
        PathRewrite(
            file_path="Store.cs",
            line=1,
            kind=PathFixKind.CASE,
            original="users.txt",
            rewritten="Users.txt",
            not_rewritten_reason=PathNotRewrittenReason.NO_MATCH,
        )


def test_unresolved_paths_are_reported_by_the_execution() -> None:
    ok = ExecutionResult(build_status=StepStatus.SUCCEEDED, run_status=StepStatus.SUCCEEDED)

    assert not ok.has_unresolved_paths
    assert not ok.model_copy(
        update={"path_rewrites": (path_rewrite("users.txt"),)}
    ).has_unresolved_paths
    mixed = (path_rewrite("users.txt"), path_rewrite(None))
    assert ok.model_copy(update={"path_rewrites": mixed}).has_unresolved_paths


def test_execution_with_path_rewrites_round_trips_through_json() -> None:
    execution = ExecutionResult(
        build_status=StepStatus.SUCCEEDED,
        run_status=StepStatus.SUCCEEDED,
        path_rewrites=(path_rewrite("users.txt"), path_rewrite(None)),
    )

    assert ExecutionResult.model_validate_json(execution.model_dump_json()) == execution


def test_probe_whose_route_was_not_found_was_never_sent() -> None:
    probe = builders.probe(path="/api/users", path_alternatives=("/api/user",))

    unsent = ProbeResult(
        probe=probe, passed=False, route_resolution=RouteResolution.ROUTE_NOT_FOUND
    )

    assert unsent.sent_path is None and not unsent.passed
    with pytest.raises(ValidationError, match="never sent"):
        ProbeResult(
            probe=probe,
            passed=False,
            sent_path="/api/users",
            route_resolution=RouteResolution.ROUTE_NOT_FOUND,
        )


def test_sent_path_must_be_a_candidate_of_the_probe() -> None:
    probe = builders.probe(path="/api/users", path_alternatives=("/api/user",))

    sent = ProbeResult(
        probe=probe,
        actual_status=200,
        passed=True,
        sent_path="/api/user",
        route_resolution=RouteResolution.RESOLVED,
    )

    assert sent.sent_path == "/api/user"
    with pytest.raises(ValidationError, match="candidate paths"):
        ProbeResult(probe=probe, actual_status=200, passed=True, sent_path="/api/people")


def test_probe_result_defaults_keep_older_results_valid() -> None:
    result = ProbeResult.model_validate(
        {"probe": builders.probe().model_dump(mode="json"), "actual_status": 200, "passed": True}
    )

    assert (result.sent_path, result.route_resolution) == (None, RouteResolution.UNAVAILABLE)
    assert result.controller is None


def test_older_execution_without_controller_fields_still_loads() -> None:
    execution = ExecutionResult.model_validate(
        {"build_status": "succeeded", "run_status": "succeeded", "probe_results": []}
    )

    assert (execution.primary_controller, execution.serving_controllers) == (None, ())
    assert (execution.target_framework, execution.framework_outdated) == (None, False)


def test_only_a_resolved_probe_has_a_controller() -> None:
    probe = builders.probe(path="/api/users")

    with pytest.raises(ValidationError, match="only a probe resolved"):
        ProbeResult(
            probe=probe,
            passed=False,
            route_resolution=RouteResolution.ROUTE_NOT_FOUND,
            controller="UsersController",
        )


def test_primary_controller_must_be_one_that_can_serve() -> None:
    with pytest.raises(ValidationError, match="primary_controller"):
        ExecutionResult(
            build_status=StepStatus.SUCCEEDED,
            run_status=StepStatus.SUCCEEDED,
            primary_controller="UsersController",
            serving_controllers=("UserController",),
        )


def test_body_adaptation_is_mismatched_only_for_unmatched_or_unconvertible_fields() -> None:
    def adaptation(*outcomes: BodyFieldOutcome) -> BodyAdaptation:
        notes = tuple(BodyFieldNote(field=f"f{i}", outcome=o) for i, o in enumerate(outcomes))
        return BodyAdaptation(adapted=True, model="User", notes=notes)

    assert not adaptation(BodyFieldOutcome.MATCHED, BodyFieldOutcome.FILLED).mismatched
    assert not adaptation(BodyFieldOutcome.CONVERTED).mismatched
    assert adaptation(BodyFieldOutcome.MATCHED, BodyFieldOutcome.UNMATCHED).mismatched
    assert adaptation(BodyFieldOutcome.NOT_CONVERTIBLE).mismatched
    assert not BodyAdaptation(adapted=False, reason="class User not found").mismatched


def test_probe_that_was_not_sent_has_no_sent_body() -> None:
    with pytest.raises(ValidationError, match="no sent_body"):
        ProbeResult(
            probe=builders.probe(),
            passed=False,
            route_resolution=RouteResolution.ROUTE_NOT_FOUND,
            sent_body="{}",
        )


def test_older_probe_result_without_body_fields_still_loads() -> None:
    result = ProbeResult.model_validate(
        {"probe": builders.probe().model_dump(mode="json"), "actual_status": 200, "passed": True}
    )

    assert (result.sent_body, result.body_adaptation, result.body_mismatched) == (
        None,
        None,
        False,
    )


def test_capture_result_has_a_value_or_a_failure() -> None:
    assert CaptureResult(name="userId", value="3").failure is None
    with pytest.raises(ValidationError, match="either a value or a failure"):
        CaptureResult(name="userId")
    with pytest.raises(ValidationError, match="either a value or a failure"):
        CaptureResult(name="userId", value="3", failure=CaptureFailure.NO_JSON)


def test_sent_path_may_fill_the_candidates_variables() -> None:
    probe = builders.probe(path="/api/users/{userId}", path_alternatives=("/api/user/{userId}",))

    def sent(path: str) -> ProbeResult:
        return ProbeResult(
            probe=probe,
            actual_status=200,
            passed=True,
            sent_path=path,
            route_resolution=RouteResolution.RESOLVED,
        )

    assert sent("/api/user/3").sent_path == "/api/user/3"
    with pytest.raises(ValidationError, match="candidate paths"):
        sent("/api/users/3/orders")
    with pytest.raises(ValidationError, match="candidate paths"):
        sent("/api/people/3")


def test_probe_with_a_missing_dependency_was_never_sent() -> None:
    probe = builders.probe(path="/api/users/{userId}")

    unsent = ProbeResult(
        probe=probe, passed=False, route_resolution=RouteResolution.DEPENDENCY_MISSING
    )

    assert unsent.sent_path is None
    with pytest.raises(ValidationError, match="never sent"):
        ProbeResult(
            probe=probe,
            actual_status=200,
            passed=True,
            route_resolution=RouteResolution.DEPENDENCY_MISSING,
        )
