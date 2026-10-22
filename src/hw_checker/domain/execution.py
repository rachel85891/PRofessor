"""Execution models: the outcome of building, running and probing a repo (design §5, §9)."""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from hw_checker.domain.assignment import HttpProbe
from hw_checker.domain.base import DomainModel, HttpStatusCode, Identifier, NonBlankStr

MAX_LOGS_EXCERPT_CHARS = 8000
"""Upper bound for a log excerpt kept in state and reports; full logs stay on disk (§7)."""

MAX_PROBE_ERROR_CHARS = 500
"""Upper bound for the short error text of a single probe."""

MAX_PATH_TEXT_CHARS = 1000
"""Upper bound for a path text recorded from student code."""


class FailureKind(StrEnum):
    """Why a build or run failed (design §5). Choosing the kind is the runner's job."""

    BUILD_FAILED = "build_failed"
    STARTUP_FAILED = "startup_failed"
    DB_UNREACHABLE = "db_unreachable"
    HARDCODED_CONNECTION_UNRESOLVED = "hardcoded_connection_unresolved"
    TIMEOUT = "timeout"
    ENVIRONMENT_ERROR = "environment_error"
    PROJECT_NOT_FOUND = "project_not_found"
    """No .csproj, or not exactly one Web project; flagged for manual review."""

    @property
    def is_student_issue(self) -> bool:
        """True only for kinds that count against the student; the rest go to manual review."""
        return self in (FailureKind.BUILD_FAILED, FailureKind.STARTUP_FAILED)


class StepStatus(StrEnum):
    """Outcome of one execution step (prepare, build or run)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProbeResult(DomainModel):
    """The result of one HTTP probe against the running app.

    The probe carries the requirement and assignment it belongs to.
    """

    probe: HttpProbe
    actual_status: HttpStatusCode | None = None
    passed: bool
    error: str = Field(default="", max_length=MAX_PROBE_ERROR_CHARS)

    @property
    def requirement_id(self) -> str:
        """The requirement this probe result belongs to."""
        return self.probe.requirement_id

    @property
    def assignment_id(self) -> str:
        """The assignment this probe result belongs to."""
        return self.probe.assignment_id

    @model_validator(mode="after")
    def _passed_matches_status(self) -> Self:
        expected_pass = self.actual_status in self.probe.allowed_statuses
        if self.passed != expected_pass:
            raise ValueError("passed must be true exactly when actual_status is an allowed status")
        return self


class PathFixKind(StrEnum):
    """Why a file path in the student's code had to be adapted to run in the container."""

    ABSOLUTE = "absolute"
    """A hard-coded absolute host path (`C:\\...`, `\\\\server\\...`)."""
    CASE = "case"
    """A relative path whose letter case differs from the real file (Windows ignores case)."""


class PathNotRewrittenReason(StrEnum):
    """Why a detected path could not be rewritten safely."""

    NO_MATCH = "no_match"
    SEVERAL_MATCHES = "several_matches"
    DYNAMIC = "dynamic"
    """The path is built with interpolation, so there is no single literal to rewrite."""


class PathRewrite(DomainModel):
    """One path detected in the student's code, and what the runner did with it.

    The rewrite is made only in the runner's temporary copy (design §9, step 4); the
    student's working copy and repo are never changed, and fixes never include it.
    """

    file_path: NonBlankStr
    """The source file, relative to the working copy, with forward slashes."""
    line: int = Field(ge=1)
    kind: PathFixKind
    original: str = Field(min_length=1, max_length=MAX_PATH_TEXT_CHARS)
    rewritten: str | None = Field(default=None, max_length=MAX_PATH_TEXT_CHARS)
    """The project-relative path used instead, or None when the path was not rewritten."""
    not_rewritten_reason: PathNotRewrittenReason | None = None

    @model_validator(mode="after")
    def _rewritten_or_reason(self) -> Self:
        if (self.rewritten is None) == (self.not_rewritten_reason is None):
            raise ValueError("a path is either rewritten or has a not_rewritten_reason")
        return self


class ExecutionResult(DomainModel):
    """Prepare, build and run outcome for one repo, with probe results and a log excerpt.

    `prepare` is project discovery and copying the code in; `build` is restore and publish.
    The steps run in that order, and the steps after a failed step are skipped.
    """

    prepare_status: StepStatus = StepStatus.SUCCEEDED
    build_status: StepStatus
    run_status: StepStatus
    failure_kind: FailureKind | None = None
    logs_excerpt: str = Field(default="", max_length=MAX_LOGS_EXCERPT_CHARS)
    logs_path: Path | None = None
    probe_results: tuple[ProbeResult, ...] = ()
    path_rewrites: tuple[PathRewrite, ...] = ()
    """Paths in the student's code that were adapted (or could not be) to run in Docker."""

    @property
    def steps(self) -> tuple[StepStatus, StepStatus, StepStatus]:
        """The step statuses in execution order: prepare, build, run."""
        return (self.prepare_status, self.build_status, self.run_status)

    @property
    def has_unresolved_paths(self) -> bool:
        """True when a detected path could not be rewritten, so the run may fail for that."""
        return any(rewrite.rewritten is None for rewrite in self.path_rewrites)

    @model_validator(mode="after")
    def _failure_kind_matches_steps(self) -> Self:
        failed = StepStatus.FAILED in self.steps
        if failed and self.failure_kind is None:
            raise ValueError("a failed step needs a failure_kind")
        if not failed and self.failure_kind is not None:
            raise ValueError("failure_kind is only allowed when a step failed")
        if (
            self.failure_kind is FailureKind.PROJECT_NOT_FOUND
            and self.prepare_status is not StepStatus.FAILED
        ):
            raise ValueError("project_not_found requires a failed prepare step")
        return self

    @model_validator(mode="after")
    def _steps_after_a_failure_are_skipped(self) -> Self:
        if StepStatus.FAILED in self.steps:
            first_failed = self.steps.index(StepStatus.FAILED)
            if any(step is not StepStatus.SKIPPED for step in self.steps[first_failed + 1 :]):
                raise ValueError("steps after a failed step must be skipped")
        return self

    @model_validator(mode="after")
    def _probes_only_after_successful_run(self) -> Self:
        if self.probe_results and self.run_status is not StepStatus.SUCCEEDED:
            raise ValueError("probe_results require a successful run")
        return self


class ExecutionRequest(DomainModel):
    """Everything the runner needs to prepare, build, run and probe one repo."""

    run_id: Identifier
    repo_id: Identifier
    working_copy: Path
    probes: tuple[HttpProbe, ...] = ()
    environment: tuple[tuple[NonBlankStr, str], ...] = ()
    """Extra environment variables for the running app (for example connection strings)."""
    logs_dir: Path

    @model_validator(mode="after")
    def _environment_keys_are_unique(self) -> Self:
        keys = [key for key, _ in self.environment]
        if len(set(keys)) != len(keys):
            raise ValueError("environment keys must be unique")
        return self
