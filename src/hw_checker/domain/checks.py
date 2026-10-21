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
    without an execution result, so `execution` is optional.
    """

    repo_dir: Path
    assignment: Assignment
    requirement: Requirement
    execution: ExecutionResult | None = None

    @model_validator(mode="after")
    def _requirement_belongs_to_assignment(self) -> Self:
        if self.requirement not in self.assignment.requirements:
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
