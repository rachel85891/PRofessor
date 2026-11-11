import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hw_checker.domain import (
    AssignmentResult,
    AssignmentVersion,
    FindingBrief,
    FixOutcome,
    LlmUsage,
    NotEvaluated,
    NotEvaluatedReason,
    PublishOutcome,
    PublishRecord,
    RepoRunResult,
    RepoRunStatus,
    RepoSummary,
    Run,
    RunStatus,
    RunSummary,
    Severity,
    new_run_id,
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
        "body_mismatch",
        "dependency_failed",
        "windows_path",
        "assignment_not_applicable",
        "assignment_condition_unknown",
        "awaiting_reviewer",
        "evidence_unavailable",
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


def test_new_run_id_starts_with_the_utc_start_time() -> None:
    first, second = new_run_id(STARTED_AT), new_run_id(STARTED_AT)

    assert re.fullmatch(r"20261020-090000-[0-9a-f]{6}", first)
    assert first != second


def test_run_summary_counts_findings_by_severity_and_skipped_checks() -> None:
    findings = (
        builders.finding(id="f-1", severity=Severity.MAJOR, assignment_id="hw01"),
        builders.finding(id="f-2", severity=Severity.MINOR, assignment_id="hw01"),
        builders.finding(id="f-3", severity=Severity.MAJOR, assignment_id="_general"),
    )
    checked = repo_run(
        assignment_results=(
            AssignmentResult(
                assignment_id="_general",
                findings=findings[2:],
                not_evaluated=(
                    NotEvaluated(
                        requirement_id="gen-build", reason=NotEvaluatedReason.BUILD_FAILED
                    ),
                ),
            ),
            AssignmentResult(assignment_id="hw01", findings=findings[:2]),
        )
    )
    failed = repo_run(
        repo_run_id="run-1/other",
        repo=builders.repo_target(repo_id="other-api"),
        status=RepoRunStatus.FAILED,
        errors=("fetch_repo: repository not found",),
    )
    run = Run(
        run_id="run-1",
        assignment_id="hw01",
        started_at=STARTED_AT,
        status=RunStatus.COMPLETED,
        repo_runs=(checked, failed),
    )

    summary = RunSummary.from_run(run)

    brief = FindingBrief(
        requirement_id="hw03-r2",
        severity=Severity.MAJOR,
        message="The action returns 200 with null.",
    )
    assert summary == RunSummary(
        run_id="run-1",
        assignment_id="hw01",
        status=RunStatus.COMPLETED,
        started_at=STARTED_AT,
        repos=(
            RepoSummary(
                repo_id="dana-api",
                status=RepoRunStatus.DONE,
                major=2,
                minor=1,
                not_evaluated=1,
                findings=(
                    brief,
                    brief,
                    brief.model_copy(update={"severity": Severity.MINOR}),
                ),
            ),
            RepoSummary(
                repo_id="other-api",
                status=RepoRunStatus.FAILED,
                errors=("fetch_repo: repository not found",),
            ),
        ),
    )


def summary_run(*repo_ids: str) -> Run:
    return Run(
        run_id="run-1",
        assignment_id="hw01",
        started_at=STARTED_AT,
        status=RunStatus.COMPLETED,
        repo_runs=tuple(
            repo_run(repo_run_id=f"run-1/{repo_id}", repo=builders.repo_target(repo_id=repo_id))
            for repo_id in repo_ids
        ),
    )


def test_run_summary_follows_the_roster_order_not_the_save_order() -> None:
    run = summary_run("chana-api", "avigail-api", "extra-api", "batya-api")

    summary = RunSummary.from_run(run, repo_order=("avigail-api", "batya-api", "chana-api"))

    assert [line.repo_id for line in summary.repos] == [
        "avigail-api",
        "batya-api",
        "chana-api",
        "extra-api",
    ]


def test_run_summary_carries_report_paths() -> None:
    run = summary_run("avigail-api", "batya-api")

    summary = RunSummary.from_run(run, report_paths={"batya-api": Path("r/batya-api.md")})

    assert [line.report_path for line in summary.repos] == [None, Path("r/batya-api.md")]


def test_publish_records_go_to_their_repos_and_the_last_one_wins() -> None:
    summary = RunSummary.from_run(summary_run("avigail-api", "batya-api"))
    first, last = (
        PublishRecord(
            repo_run_id="run-1/batya-api",
            repo_id="batya-api",
            branch_name="hw-review/hw01-2026-11-01",
            published_at=STARTED_AT,
            outcome=outcome,
            message="moved",
        )
        for outcome in (PublishOutcome.FAILED, PublishOutcome.STALE)
    )

    updated = summary.with_publish_records([first, last])

    assert [line.publication for line in updated.repos] == [None, last]


def test_llm_totals_count_only_this_runs_calls_once_each() -> None:
    summary = RunSummary.from_run(summary_run("avigail-api"))
    usages = [
        LlmUsage(run_id="run-1", call_id="a", role="fixer", input_tokens=100, output_tokens=10),
        LlmUsage(run_id="run-1", call_id="a", role="fixer", input_tokens=100, output_tokens=10),
        LlmUsage(run_id="run-1", call_id="b", role="judge", input_tokens=50, output_tokens=5),
        LlmUsage(run_id="run-0", call_id="c", role="writer", input_tokens=70, output_tokens=7),
    ]
    outcomes = [
        FixOutcome(repo_id="avigail-api", proposal_id="run-1/avigail-api/fix"),
        FixOutcome(repo_id="batya-api", reason="No PR."),
    ]

    totals = summary.with_fixer(usages, outcomes)

    assert (totals.llm_calls, totals.llm_input_tokens, totals.llm_output_tokens) == (2, 150, 15)
    assert totals.proposals == 1


def test_finding_brief_is_the_first_line_of_the_english_message() -> None:
    finding = builders.finding(message_en="  POST /api/users: expected 201, got 500.\nMore.  ")
    run = summary_run("dana-api").model_copy(
        update={
            "repo_runs": (
                repo_run(
                    assignment_results=(
                        AssignmentResult(assignment_id="hw03", findings=(finding,)),
                    )
                ),
            )
        }
    )

    [line] = RunSummary.from_run(run).repos

    assert [brief.message for brief in line.findings] == ["POST /api/users: expected 201, got 500."]


def test_a_repo_summary_lists_the_assignments_whose_condition_is_unknown() -> None:
    def skipped(assignment_id: str, reason: NotEvaluatedReason) -> AssignmentResult:
        return AssignmentResult(
            assignment_id=assignment_id,
            not_evaluated=(
                NotEvaluated(requirement_id=f"{assignment_id}-r1", reason=reason, detail="why"),
            ),
        )

    results = (
        AssignmentResult(assignment_id="hw01"),
        skipped("hw02", NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE),
        skipped("hw03", NotEvaluatedReason.ASSIGNMENT_CONDITION_UNKNOWN),
    )
    repo_run = RepoRunResult(
        repo_run_id="run-1/dana-api",
        repo=builders.repo_target(),
        status=RepoRunStatus.DONE,
        assignment_results=results,
    )

    line = RepoSummary.from_repo_run(repo_run)

    assert [r.not_checked is None for r in results] == [True, False, False]
    assert [(u.repo_id, u.assignment_id, u.detail) for u in line.unknown_conditions] == [
        ("dana-api", "hw03", "why")
    ]


def test_an_assignment_that_does_not_apply_is_not_counted_as_skipped_checks() -> None:
    def entry(requirement_id: str, reason: NotEvaluatedReason) -> NotEvaluated:
        return NotEvaluated(requirement_id=requirement_id, reason=reason)

    repo_run = RepoRunResult(
        repo_run_id="run-1/dana-api",
        repo=builders.repo_target(),
        status=RepoRunStatus.DONE,
        assignment_results=(
            AssignmentResult(
                assignment_id="hw02",
                not_evaluated=(entry("hw02-r1", NotEvaluatedReason.EVIDENCE_UNAVAILABLE),),
            ),
            AssignmentResult(
                assignment_id="hw03",
                not_evaluated=(
                    entry("hw03-r1", NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE),
                    entry("hw03-r2", NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE),
                ),
            ),
            AssignmentResult(
                assignment_id="hw04",
                not_evaluated=(entry("hw04-r1", NotEvaluatedReason.ASSIGNMENT_CONDITION_UNKNOWN),),
            ),
        ),
    )

    line = RepoSummary.from_repo_run(repo_run)

    assert line.not_evaluated == 1
    assert [item.assignment_id for item in line.unknown_conditions] == ["hw04"]
