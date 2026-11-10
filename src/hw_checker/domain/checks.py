"""Check inputs, and descriptions of the registered checks for listing and editing forms."""

from pathlib import Path
from typing import Self

from pydantic import model_validator

from hw_checker.domain.architecture_evidence import ArchitectureEvidence
from hw_checker.domain.assignment import Assignment, ParamValue, Requirement
from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr
from hw_checker.domain.execution import ExecutionResult
from hw_checker.domain.run import NotEvaluatedReason


class TrackedFiles(DomainModel):
    """What git tracks in a working copy, read from its index (the checked commit).

    `paths` are repo-relative with forward slashes. `ignored` are the tracked paths that the
    repo's own `.gitignore` files match (git decides, not text matching): files committed
    before `.gitignore` listed them.
    """

    paths: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _ignored_are_tracked(self) -> Self:
        if not set(self.ignored) <= set(self.paths):
            raise ValueError("every ignored path must be a tracked path")
        return self


class CheckInput(DomainModel):
    """What evaluating one requirement receives.

    `repo_dir` is the student's working copy, which checks only read. Structure checks run
    without an execution result, so `execution` is optional. `files`, when set, lists the
    repo's files at another commit (repo-relative POSIX paths); checks that only look at
    file names read it instead of the working copy. The requirement is one of the
    assignment's, or its `ends_when`, `applies_when` or `skip_when` condition. `tracked` is
    None when git's state could not be read; checks that need it are then not evaluated.
    `architecture` is the working copy's local evidence (projects, references, C# signals),
    read only for a check that declares it reads it; None otherwise, or when it could not
    be read (such a check is then not evaluated).
    """

    repo_dir: Path
    assignment: Assignment
    requirement: Requirement
    execution: ExecutionResult | None = None
    files: tuple[str, ...] | None = None
    tracked: TrackedFiles | None = None
    architecture: ArchitectureEvidence | None = None

    @model_validator(mode="after")
    def _requirement_belongs_to_assignment(self) -> Self:
        if (
            self.requirement not in self.assignment.requirements
            and self.requirement != self.assignment.end_requirement
            and self.requirement != self.assignment.condition_requirement
        ):
            raise ValueError(
                f"requirement {self.requirement.id!r} is not part of assignment "
                f"{self.assignment.id!r}"
            )
        return self


class CheckPassed(DomainModel):
    """A check passed, and says what it saw (`DI has started (3 registrations)`), for a
    condition's report. A check that has nothing to say returns no findings instead."""

    message: NonBlankStr


class ConditionOutcome(DomainModel):
    """A condition's check on one repo: it holds (passed), it does not (findings), or it
    could not be evaluated (`holds` is None, with `reason`). `message` is the check's own
    words: what it saw, or its findings' messages."""

    holds: bool | None
    message: str = ""
    reason: NotEvaluatedReason | None = None

    @model_validator(mode="after")
    def _reason_when_unknown(self) -> Self:
        if (self.holds is None) != (self.reason is not None):
            raise ValueError("a reason is given exactly when the condition is unknown")
        return self


class CheckParamDescription(DomainModel):
    """One parameter of a check: its name, a plain type name, and whether it is required."""

    name: Identifier
    type: NonBlankStr
    required: bool
    default: ParamValue | None = None


class CheckDescription(DomainModel):
    """A registered check and the parameters it takes."""

    name: Identifier
    description: NonBlankStr
    params: tuple[CheckParamDescription, ...] = ()
    reads_file_list: bool = False
    """True when the check looks at file names only, so it can run on a commit's file list
    (for `ends_when`) as well as on the working copy."""
    reads_architecture: bool = False
    """True when the check reads the repo's architecture evidence (`CheckInput.architecture`)."""
    params_schema: str = "{}"
    """The JSON Schema of the check's params model, as compact JSON text (for the author)."""
