"""Adding an assignment (ADR 0023, design §8.3): the developer's intake.

The developer writes `assignments/<id>/intake.yaml` (this model) and `task.md`; the
`author` role drafts the requirements and review rules from them.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from hw_checker.domain.assignment import Assignment, Severity, TaskEnd
from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr

LATEST: Literal["latest"] = "latest"
"""`check_commit: latest`: the round checks the repo's latest commit."""


class IntakeItem(DomainModel):
    """One thing the developer asks for, in their own words, with an optional severity."""

    text: NonBlankStr
    severity: Severity | None = None


class IntakeRequirements(DomainModel):
    """The task's requirements under the four headings of the intake, each matching the
    kinds of requirement the author drafts for it."""

    return_values: tuple[IntakeItem, ...] = ()
    """Return values and statuses: `http` probes."""
    clean_code: tuple[IntakeItem, ...] = ()
    """Clean code: naming checks and `code` requirements."""
    architecture: tuple[IntakeItem, ...] = ()
    """Architecture: the architecture checks."""
    general: tuple[IntakeItem, ...] = ()
    """Anything else, or for `_general`."""


class LecturerDecisions(DomainModel):
    """What the lecturer decided for this assignment."""

    accepted_alternatives: tuple[NonBlankStr, ...] = ()
    not_to_mention: tuple[NonBlankStr, ...] = ()
    required_statuses: tuple[NonBlankStr, ...] = ()


class EndsWhenChoice(DomainModel):
    """`check_commit: {ends_when: ...}`: the round checks the last commit before the next
    task starts, as an assignment's `ends_when` says."""

    ends_when: TaskEnd


class AssignmentIntake(DomainModel):
    """`assignments/<id>/intake.yaml`: what the developer gives for a new assignment.

    `taught` and `not_yet_taught` start as the previous assignment's (by `order`), when it
    has an intake. `short_name` is the name students know the assignment by; it is derived
    from the id (`hw02` → `HW2`), so the intake may only confirm it.
    """

    id: Identifier
    order: int = Field(ge=1)
    title: NonBlankStr
    short_name: NonBlankStr | None = None
    check_commit: Literal["latest"] | EndsWhenChoice = LATEST
    taught: tuple[NonBlankStr, ...] = ()
    not_yet_taught: tuple[NonBlankStr, ...] = ()
    lecturer_decisions: LecturerDecisions = LecturerDecisions()
    requirements: IntakeRequirements = IntakeRequirements()

    @model_validator(mode="after")
    def _short_name_is_derived(self) -> Self:
        derived = self.derived_short_name
        if self.short_name is not None and self.short_name != derived:
            raise ValueError(f"short_name must be {derived}: it is derived from the id {self.id!r}")
        return self

    @property
    def derived_short_name(self) -> str:
        """The short name the rounds use, derived from the id."""
        return Assignment(
            id=self.id, order=self.order, title=self.title, requirements=()
        ).short_name

    @property
    def ends_when(self) -> TaskEnd | None:
        """The `ends_when` of the drafted assignment; None when the latest commit is checked."""
        return None if isinstance(self.check_commit, str) else self.check_commit.ends_when

    def items(self) -> tuple[tuple[str, str], ...]:
        """Every item a drafted requirement or rule must cover, as (key, text): the four
        requirement lists, the lecturer's decisions and `not_yet_taught`, keyed like
        `requirements.return_values.1` (numbered from 1)."""
        lists: list[tuple[str, tuple[str, ...]]] = [
            (f"requirements.{name}", tuple(item.text for item in getattr(self.requirements, name)))
            for name in IntakeRequirements.model_fields
        ]
        lists += [
            (f"lecturer_decisions.{name}", getattr(self.lecturer_decisions, name))
            for name in LecturerDecisions.model_fields
        ]
        lists.append(("not_yet_taught", self.not_yet_taught))
        return tuple(
            (f"{prefix}.{number}", text)
            for prefix, texts in lists
            for number, text in enumerate(texts, start=1)
        )
