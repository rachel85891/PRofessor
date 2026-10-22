from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from hw_checker.domain import (
    AssignmentResult,
    AssignmentVersion,
    NotEvaluated,
    NotEvaluatedReason,
    RepoRunResult,
    RepoRunStatus,
    Run,
    RunStatus,
)
from tests.unit.domain import builders

STARTED_AT = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def repo_run(**overrides: Any) -> RepoRunResult:
    values: dict[str, Any] = {
        "repo_run_id": "run-1/dana-api",
        "repo": builders.repo_target(),
        "commit_sha": "0f6db0d",
        "status": RepoRunStatus.DONE,
    }
    return RepoRunResult.model_validate(values | overrides)


def test_repo_run_result_builds_with_assignment_results() -> None:
    result = repo_run(
        assignment_results=(
            AssignmentResult(assignment_id="hw01"),
            AssignmentResult(assignment_id="hw02", findings=(builders.finding(),)),
        ),
        score=87.5,
    )

    assert len(result.assignment_results) == 2


def test_failed_repo_run_without_commit_records_errors() -> None:
    result = repo_run(commit_sha=None, status=RepoRunStatus.FAILED, errors=("Clone failed",))

    assert result.errors == ("Clone failed",)


def test_repo_run_result_rejects_duplicate_assignment_results() -> None:
    with pytest.raises(ValidationError, match="one entry per assignment_id"):
        repo_run(
            assignment_results=(
                AssignmentResult(assignment_id="hw01"),
                AssignmentResult(assignment_id="hw01"),
            )
        )


def test_repo_run_result_rejects_negative_score() -> None:
    with pytest.raises(ValidationError):
        repo_run(score=-1)


def test_repo_run_result_rejects_empty_commit_sha() -> None:
    with pytest.raises(ValidationError):
        repo_run(commit_sha="")


def test_repo_run_statuses() -> None:
    assert {status.value for status in RepoRunStatus} == {"queued", "running", "done", "failed"}


def test_run_builds() -> None:
    run = Run(
        run_id="run-1",
        assignment_id="hw03",
        started_at=STARTED_AT,
        status=RunStatus.RUNNING,
        repo_runs=(repo_run(),),
    )

    assert run.repo_runs[0].repo.repo_id == "dana-api"


def test_run_accepts_non_utc_aware_datetime() -> None:
    israel = timezone(timedelta(hours=3))
    started_at = datetime(2026, 10, 20, 12, 0, tzinfo=israel)

    run = Run(run_id="run-1", assignment_id="hw03", started_at=started_at, status=RunStatus.RUNNING)

    assert run.started_at == STARTED_AT


def test_run_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        Run(
            run_id="run-1",
            assignment_id="hw03",
            started_at=datetime(2026, 10, 20, 9, 0),
            status=RunStatus.RUNNING,
        )


def test_run_rejects_naive_iso_string() -> None:
    with pytest.raises(ValidationError):
        Run.model_validate(
            {
                "run_id": "run-1",
                "assignment_id": "hw03",
                "started_at": "2026-10-20T09:00:00",
                "status": "running",
            }
        )


def test_run_rejects_duplicate_repos() -> None:
    with pytest.raises(ValidationError, match="unique"):
        Run(
            run_id="run-1",
            assignment_id="hw03",
            started_at=STARTED_AT,
            status=RunStatus.COMPLETED,
            repo_runs=(repo_run(), repo_run(repo_run_id="run-1/other")),
        )


def test_run_is_frozen() -> None:
    run = Run(run_id="run-1", assignment_id="hw03", started_at=STARTED_AT, status=RunStatus.RUNNING)

    with pytest.raises(ValidationError):
        run.status = RunStatus.COMPLETED  # type: ignore[misc]


HASH_A = "a" * 64
HASH_B = "b" * 64


def test_run_records_assignment_versions() -> None:
    versions = (
        AssignmentVersion(assignment_id="_general", source_hash=HASH_A),
        AssignmentVersion(assignment_id="hw01", source_hash=HASH_B),
    )

    run = Run(
        run_id="run-1",
        assignment_id="hw01",
        started_at=STARTED_AT,
        status=RunStatus.RUNNING,
        assignment_versions=versions,
    )

    assert run.assignment_versions == versions


def test_assignment_versions_default_to_empty() -> None:
    run = Run(run_id="run-1", assignment_id="hw01", started_at=STARTED_AT, status=RunStatus.RUNNING)

    assert run.assignment_versions == ()


def test_assignment_versions_must_have_unique_ids() -> None:
    with pytest.raises(ValidationError, match="one entry per assignment_id"):
        Run(
            run_id="run-1",
            assignment_id="hw01",
            started_at=STARTED_AT,
            status=RunStatus.RUNNING,
            assignment_versions=(
                AssignmentVersion(assignment_id="hw01", source_hash=HASH_A),
                AssignmentVersion(assignment_id="hw01", source_hash=HASH_B),
            ),
        )


@pytest.mark.parametrize("source_hash", ["", "abc", "A" * 64, "g" * 64, "a" * 63])
def test_assignment_version_hash_must_be_sha256_hex(source_hash: str) -> None:
    with pytest.raises(ValidationError):
        AssignmentVersion(assignment_id="hw01", source_hash=source_hash)


def test_assignment_result_records_not_evaluated_requirements() -> None:
    result = AssignmentResult(
        assignment_id="hw01",
        findings=(builders.finding(requirement_id="hw01-r1"),),
        not_evaluated=(
            NotEvaluated(requirement_id="hw01-r2", reason=NotEvaluatedReason.BUILD_FAILED),
            NotEvaluated(requirement_id="hw01-r3", reason=NotEvaluatedReason.NO_EXECUTION),
        ),
    )

    assert [entry.reason for entry in result.not_evaluated] == [
        NotEvaluatedReason.BUILD_FAILED,
        NotEvaluatedReason.NO_EXECUTION,
    ]


def test_not_evaluated_reasons() -> None:
    assert {reason.value for reason in NotEvaluatedReason} == {
        "no_execution",
        "build_failed",
        "run_not_healthy",
        "environment_issue",
        "route_not_found",
    }


def test_a_requirement_cannot_be_both_failed_and_not_evaluated() -> None:
    with pytest.raises(ValidationError, match="both failed and not evaluated: hw01-r1"):
        AssignmentResult(
            assignment_id="hw01",
            findings=(builders.finding(requirement_id="hw01-r1"),),
            not_evaluated=(
                NotEvaluated(requirement_id="hw01-r1", reason=NotEvaluatedReason.RUN_NOT_HEALTHY),
            ),
        )


def test_not_evaluated_lists_each_requirement_once() -> None:
    entry = NotEvaluated(requirement_id="hw01-r2", reason=NotEvaluatedReason.BUILD_FAILED)

    with pytest.raises(ValidationError, match="each requirement once"):
        AssignmentResult(assignment_id="hw01", not_evaluated=(entry, entry))


def test_not_evaluated_rejects_unknown_reason() -> None:
    with pytest.raises(ValidationError):
        NotEvaluated.model_validate({"requirement_id": "hw01-r2", "reason": "skipped"})
