"""Adding an assignment (ADR 0023, design §8.3): the developer's intake and the author's
draft.

The developer writes `assignments/<id>/intake.yaml` (`AssignmentIntake`) and `task.md`;
the `author` role drafts the requirements and review rules from them (`AuthorReply`).
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from hw_checker.domain.assignment import (
    VARIABLE_NAME_PATTERN,
    Assignment,
    ExpectedJson,
    Feedback,
    HttpMethod,
    ParamValue,
    ProbePath,
    RequirementKind,
    Severity,
    TaskEnd,
)
from hw_checker.domain.base import DomainModel, HttpStatusCode, Identifier, NonBlankStr
from hw_checker.domain.course import FixExample, ItemStatus, Origin, ReviewRule

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


class CaptureSpec(DomainModel):
    """A probe's capture as `requirements.yaml` writes it: `{name, from}`."""

    name: Annotated[str, StringConstraints(pattern=rf"^{VARIABLE_NAME_PATTERN}$")]
    source: Annotated[
        str, StringConstraints(pattern=rf"^(body:{VARIABLE_NAME_PATTERN}|location)$")
    ] = Field(alias="from")


class ProbeSpec(DomainModel):
    """One HTTP probe as `requirements.yaml` writes it (assignments/README.md, "Probe
    fields"). `body` is JSON text."""

    method: HttpMethod
    path: ProbePath
    path_alternatives: tuple[ProbePath, ...] = ()
    capture: CaptureSpec | None = None
    expect_json: ExpectedJson | None = None
    expect_values: tuple[NonBlankStr, ...] = ()
    body: str | None = None
    expected_status: HttpStatusCode
    allowed_statuses: tuple[HttpStatusCode, ...] = ()
    notes: str = ""


class RequirementSpec(DomainModel):
    """One requirement as `requirements.yaml` writes it (assignments/README.md,
    "Requirement fields"), with its origin and approval."""

    id: Identifier
    kind: RequirementKind
    title: NonBlankStr
    description: str = ""
    severity: Severity
    allowed_alternatives: tuple[NonBlankStr, ...] = ()
    check: Identifier | None = None
    params: dict[str, ParamValue] = Field(default_factory=dict)
    probes: tuple[ProbeSpec, ...] = ()
    depends_on: tuple[Identifier, ...] = ()
    feedback: Feedback = Feedback.FIXES_ALLOWED
    origin: Origin
    status: ItemStatus


class Drafted(DomainModel):
    """What the author adds to every drafted item: a one-line plain-English summary for
    the report, and the keys of the intake items it covers (`requirements.return_values.1`)."""

    summary: NonBlankStr
    intake_items: tuple[NonBlankStr, ...] = ()


class DraftedRequirement(RequirementSpec, Drafted):
    """A requirement the author drafted."""


class DraftedRule(ReviewRule, Drafted):
    """A review rule the author drafted."""


class DraftedExample(FixExample, Drafted):
    """A fixer example the author drafted."""


class FrameSuggestion(DomainModel):
    """A change to a prompt's frame the author suggests; only the developer applies it,
    by a commit (ADR 0023)."""

    template: NonBlankStr
    suggestion: NonBlankStr
    reason: NonBlankStr


class AuthorReply(DomainModel):
    """The `author` role's reply: the new assignment's requirements and review rules, every
    item a draft with its origin."""

    requirements: tuple[DraftedRequirement, ...]
    rules: tuple[DraftedRule, ...] = ()
    examples: tuple[DraftedExample, ...] = ()
    lenient_notes: tuple[NonBlankStr, ...] = ()
    frame_suggestions: tuple[FrameSuggestion, ...] = ()

    @property
    def items(self) -> tuple[DraftedRequirement | DraftedRule | DraftedExample, ...]:
        """Every drafted requirement, rule and example, in that order."""
        return (*self.requirements, *self.rules, *self.examples)


class DataFile(DomainModel):
    """A course or assignment data file shown to the author, by its repo path."""

    label: NonBlankStr
    text: str


class DraftFiles(DomainModel):
    """A drafted assignment's two files, as the text written to its folder."""

    requirements: str
    review_rules: str


class DryRunStatus(StrEnum):
    """What the dry run on the reference solution found."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    """There is no `reference/` folder."""
    NOT_RUN = "not_run"
    """The draft is invalid, so it was not run."""


class DryRun(DomainModel):
    """The drafted requirements on the reference solution: every one must pass.

    `failures` and `not_evaluated` are `<requirement id>: <what happened>` lines; a
    requirement that cannot be evaluated there is shown, but is not a failure.
    """

    status: DryRunStatus
    failures: tuple[str, ...] = ()
    not_evaluated: tuple[str, ...] = ()

    def failed(self, requirement_id: str) -> bool:
        """Whether `requirement_id` failed on the reference solution."""
        return any(line.startswith(f"{requirement_id}: ") for line in self.failures)


class PreviewCount(DomainModel):
    """One drafted requirement over the workspace clones: how many repos fail, pass or
    could not be evaluated. Counts only, never names."""

    requirement_id: Identifier
    failed: int = Field(ge=0)
    passed: int = Field(ge=0)
    not_evaluated: int = Field(ge=0)
    flagged: bool = False
    """It fails at least the configured share of the repos it could evaluate."""


class PreviewStatus(StrEnum):
    """Whether the preview ran."""

    RAN = "ran"
    NO_REPOS = "no_repos"
    NOT_RUN = "not_run"
    """The draft is invalid, so it was not run."""


class Preview(DomainModel):
    """The drafted deterministic requirements on the workspace clones' working copies.

    Without execution, only the requirements whose checks read files (`structure` and
    `custom`) are run; `left_out` lists the others.
    """

    status: PreviewStatus
    with_execution: bool = False
    repos: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    """Repos that could not be previewed at all."""
    counts: tuple[PreviewCount, ...] = ()
    left_out: tuple[Identifier, ...] = ()

    def count(self, requirement_id: str) -> PreviewCount | None:
        """The counts of `requirement_id`; None when it was not previewed."""
        return next((c for c in self.counts if c.requirement_id == requirement_id), None)


class RuleChanges(DomainModel):
    """The review rules and examples in effect for the new assignment, against the
    previous assignment's: new, replacing an earlier one (`new (replaces old)`), and ended."""

    new: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()
    ended: tuple[str, ...] = ()


class Coverage(DomainModel):
    """One intake item and the drafted items that cover it."""

    key: NonBlankStr
    text: NonBlankStr
    covered_by: tuple[Identifier, ...] = ()


class ValidationReport(DomainModel):
    """What the deterministic validation found (design §8.3, stage 6)."""

    problems: tuple[str, ...] = ()
    """Schema, catalog and origin problems, all listed together."""
    coverage: tuple[Coverage, ...] = ()
    dry_run: DryRun
    preview: Preview
    changes: RuleChanges = RuleChanges()

    @property
    def uncovered(self) -> tuple[Coverage, ...]:
        """The intake items no drafted item covers."""
        return tuple(item for item in self.coverage if not item.covered_by)

    @property
    def passed(self) -> bool:
        """No problem, every intake item covered, and nothing failed the reference solution."""
        return (
            not self.problems
            and not self.uncovered
            and self.dry_run.status is not DryRunStatus.FAILED
        )

    @property
    def reasons(self) -> tuple[str, ...]:
        """Why the draft goes back to the author: every problem, uncovered item and failure
        on the reference solution."""
        uncovered = tuple(
            f"intake item `{item.key}` is not covered by any requirement, rule or example: "
            f"{item.text}"
            for item in self.uncovered
        )
        failures = tuple(
            f"fails the reference solution, which must pass it: {line}"
            for line in self.dry_run.failures
        )
        return self.problems + uncovered + failures


class AuthoringAction(StrEnum):
    """The developer's answer at the authoring graph's approval."""

    APPROVE = "approve"
    REVISE = "revise"
    STOP = "stop"


class AuthoringDecision(DomainModel):
    """The resume value of the approval interrupt: approve, revise with notes, or stop."""

    action: AuthoringAction
    notes: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def _notes_go_with_revise(self) -> Self:
        if self.action is AuthoringAction.REVISE and not self.notes:
            raise ValueError("revise needs at least one note: what must change")
        if self.action is not AuthoringAction.REVISE and self.notes:
            raise ValueError("notes go with revise only")
        return self


class AuthoringPrompt(DomainModel):
    """The approval interrupt's payload: where the report is and how to answer."""

    assignment_id: Identifier
    report_path: str
    attempt: int = Field(ge=1)
    message: str = ""
    """Why the last answer was not applied; empty when there was none."""
    answer: str = (
        '{"action": "approve"}, {"action": "revise", "notes": ["what must change"]} '
        'or {"action": "stop"}'
    )


class AuthoringOutcome(StrEnum):
    """How an authoring run ended."""

    APPROVED = "approved"
    STOPPED = "stopped"
    FAILED = "failed"
    """Validation still failed after `authoring.max_attempts` drafts; nothing was written
    to the assignment's folder."""
