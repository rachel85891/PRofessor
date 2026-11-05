"""Check inputs, and descriptions of the registered checks for listing and editing forms."""

from pathlib import Path
from typing import Self

from pydantic import model_validator

from hw_checker.domain.assignment import Assignment, ParamValue, Requirement
from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr
from hw_checker.domain.execution import ExecutionResult


class CheckInput(DomainModel):
    """What evaluating one requirement receives.

    `repo_dir` is the student's working copy, which checks only read. Structure checks run
    without an execution result, so `execution` is optional. `files`, when set, lists the
    repo's files at another commit (repo-relative POSIX paths); checks that only look at
    file names read it instead of the working copy. The requirement is one of the
    assignment's, or its `ends_when` condition.
    """

    repo_dir: Path
    assignment: Assignment
    requirement: Requirement
    execution: ExecutionResult | None = None
    files: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _requirement_belongs_to_assignment(self) -> Self:
        if (
            self.requirement not in self.assignment.requirements
            and self.requirement != self.assignment.end_requirement
        ):
            raise ValueError(
                f"requirement {self.requirement.id!r} is not part of assignment "
                f"{self.assignment.id!r}"
            )
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
