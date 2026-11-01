"""Run models: one checking round and the result for each repo in it."""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from hw_checker.domain.assignment import SHA256_HEX_PATTERN, Severity
from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr
from hw_checker.domain.execution import ExecutionResult
from hw_checker.domain.findings import Finding, JudgedFinding
from hw_checker.domain.publishing import PublishRecord
from hw_checker.domain.roster import RepoTarget

RUN_ID_SUFFIX_LENGTH = 6


class RepoRunStatus(StrEnum):
    """Progress of one repo within a round (design §12)."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class RunStatus(StrEnum):
    """Progress of a whole round."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class NotEvaluatedReason(StrEnum):
    """Why a requirement could not be checked; none of these count against the student."""

    NO_EXECUTION = "no_execution"
    BUILD_FAILED = "build_failed"
    RUN_NOT_HEALTHY = "run_not_healthy"
    ENVIRONMENT_ISSUE = "environment_issue"
    ROUTE_NOT_FOUND = "route_not_found"
    """None of a probe's paths is declared in the student's code, so it was not sent."""
    BODY_MISMATCH = "body_mismatch"
    """A failing probe's body had fields that do not fit the student's model."""
    DEPENDENCY_FAILED = "dependency_failed"
    """A probe needed a value an earlier probe failed to capture, so it was not sent."""


class NotEvaluated(DomainModel):
    """A requirement that was not checked, and why."""

    requirement_id: Identifier
    reason: NotEvaluatedReason


class AssignmentResult(DomainModel):
    """The outcome of checking one assignment in one repo (before judging).

    A requirement is in `findings` (it failed), in `not_evaluated` (it could not be
    checked), or in neither (it passed); never in both.
    """

    assignment_id: Identifier
    findings: tuple[Finding, ...] = ()
    not_evaluated: tuple[NotEvaluated, ...] = ()

    @model_validator(mode="after")
    def _not_evaluated_is_consistent(self) -> Self:
        skipped = [entry.requirement_id for entry in self.not_evaluated]
        if len(set(skipped)) != len(skipped):
            raise ValueError("not_evaluated must list each requirement once")
        both = sorted(set(skipped) & {finding.requirement_id for finding in self.findings})
        if both:
            raise ValueError(f"requirements both failed and not evaluated: {', '.join(both)}")
        return self


class RepoRunResult(DomainModel):
    """The outcome of checking one repo in one round."""

    repo_run_id: Identifier
    repo: RepoTarget
    commit_sha: Identifier | None = None
    status: RepoRunStatus
    execution: ExecutionResult | None = None
    assignment_results: tuple[AssignmentResult, ...] = ()
    judged_findings: tuple[JudgedFinding, ...] = ()
    score: float | None = Field(default=None, ge=0)
    errors: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def _one_result_per_assignment(self) -> Self:
        ids = [result.assignment_id for result in self.assignment_results]
        if len(set(ids)) != len(ids):
            raise ValueError("assignment_results must have one entry per assignment_id")
        return self


class AssignmentVersion(DomainModel):
    """Which version of an assignment's requirements file a round checked (design §8)."""

    assignment_id: Identifier
    source_hash: str = Field(pattern=SHA256_HEX_PATTERN)


class Run(DomainModel):
    """One checking round for an assignment over all repos in the roster."""

    run_id: Identifier
    assignment_id: Identifier
    started_at: AwareDatetime
    status: RunStatus
    repo_runs: tuple[RepoRunResult, ...] = ()
    assignment_versions: tuple[AssignmentVersion, ...] = ()

    @model_validator(mode="after")
    def _assignment_versions_are_unique(self) -> Self:
        ids = [version.assignment_id for version in self.assignment_versions]
        if len(set(ids)) != len(ids):
            raise ValueError("assignment_versions must have one entry per assignment_id")
        return self

    @model_validator(mode="after")
    def _repos_are_unique(self) -> Self:
        run_ids = [repo_run.repo_run_id for repo_run in self.repo_runs]
        repo_ids = [repo_run.repo.repo_id for repo_run in self.repo_runs]
        if len(set(run_ids)) != len(run_ids) or len(set(repo_ids)) != len(repo_ids):
            raise ValueError("repo_runs must have unique repo_run_id and repo_id values")
        return self


def new_run_id(started_at: datetime) -> str:
    """Return a new run id: the start time in UTC and a short random suffix.

    For example `20261029-143005-3fa9c2`. Ids sort by start time.
    """
    suffix = uuid.uuid4().hex[:RUN_ID_SUFFIX_LENGTH]
    return f"{started_at:%Y%m%d-%H%M%S}-{suffix}"


class FindingBrief(DomainModel):
    """One finding in a round summary: its requirement, severity and first message line."""

    requirement_id: Identifier
    severity: Severity
    message: NonBlankStr


class RepoSummary(DomainModel):
    """One repo's line in a round summary: status, finding counts, findings and errors."""

    repo_id: Identifier
    status: RepoRunStatus
    critical: int = Field(default=0, ge=0)
    major: int = Field(default=0, ge=0)
    minor: int = Field(default=0, ge=0)
    not_evaluated: int = Field(default=0, ge=0)
    findings: tuple[FindingBrief, ...] = ()
    """Each finding (before judging), briefly, in the order the checks reported them."""
    errors: tuple[NonBlankStr, ...] = ()
    report_path: Path | None = None
    """The repo's reviewer report; None when it was not written."""
    publication: PublishRecord | None = None
    """The repo's last publish attempt in this round; None when it was not published."""

    @classmethod
    def from_repo_run(cls, repo_run: RepoRunResult, report_path: Path | None = None) -> Self:
        """Count the repo's findings (before judging) by severity, and its skipped checks."""
        findings = [
            finding for result in repo_run.assignment_results for finding in result.findings
        ]
        return cls(
            repo_id=repo_run.repo.repo_id,
            status=repo_run.status,
            critical=sum(finding.severity is Severity.CRITICAL for finding in findings),
            major=sum(finding.severity is Severity.MAJOR for finding in findings),
            minor=sum(finding.severity is Severity.MINOR for finding in findings),
            not_evaluated=sum(len(result.not_evaluated) for result in repo_run.assignment_results),
            findings=tuple(
                FindingBrief(
                    requirement_id=finding.requirement_id,
                    severity=finding.severity,
                    message=finding.message_en.strip().splitlines()[0],
                )
                for finding in findings
            ),
            errors=repo_run.errors,
            report_path=report_path,
        )


class RunSummary(DomainModel):
    """A short account of a round: its status, one line per repo and where its reports are."""

    run_id: Identifier
    assignment_id: Identifier
    status: RunStatus
    started_at: AwareDatetime | None = None
    repos: tuple[RepoSummary, ...] = ()
    reports_dir: Path | None = None
    """The folder of the round's reports; None until they are written."""
    round_report: Path | None = None
    """The round's index report (`round.md`); None until it is written."""

    @classmethod
    def from_run(
        cls,
        run: Run,
        repo_order: Sequence[str] = (),
        report_paths: Mapping[str, Path] | None = None,
    ) -> Self:
        """Summarize `run` and every repo run it holds.

        Repos are listed in `repo_order` (the roster's order); repos missing from it follow
        in the order they were saved. `report_paths` maps a repo id to its report.
        """
        position = {repo_id: index for index, repo_id in enumerate(repo_order)}
        repo_runs = sorted(
            run.repo_runs,
            key=lambda repo_run: position.get(repo_run.repo.repo_id, len(position)),
        )
        paths = report_paths or {}
        return cls(
            run_id=run.run_id,
            assignment_id=run.assignment_id,
            status=run.status,
            started_at=run.started_at,
            repos=tuple(
                RepoSummary.from_repo_run(repo_run, paths.get(repo_run.repo.repo_id))
                for repo_run in repo_runs
            ),
        )

    def with_publish_records(self, records: Sequence[PublishRecord]) -> Self:
        """Return this summary with each repo's last publish attempt among `records`."""
        latest = {record.repo_id: record for record in records}
        return self.model_copy(
            update={
                "repos": tuple(
                    line.model_copy(update={"publication": latest[line.repo_id]})
                    if line.repo_id in latest
                    else line
                    for line in self.repos
                )
            }
        )
