"""Run models: one checking round and the result for each repo in it."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from hw_checker.domain.assignment import SHA256_HEX_PATTERN
from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr
from hw_checker.domain.execution import ExecutionResult
from hw_checker.domain.findings import Finding, JudgedFinding
from hw_checker.domain.roster import RepoTarget


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
