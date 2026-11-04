"""Fixer models: the plan the model returns, what was applied, and the fixer's outcome.

The model's replies (`FixPlan`, `SelfReview`, `PullRequestText`) have no default values, so
their JSON schema lists every field as required, as structured outputs expect. Paths in a
plan are plain text: an invalid path becomes a rejected edit, not an unreadable reply.
"""

from enum import StrEnum
from pathlib import Path

from pydantic import Field

from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr


class EditKind(StrEnum):
    """What a planned edit may change (docs/fix-pr-workflow.md §3)."""

    FIX = "fix"
    """A small code change, with a `//fix: ...` line directly above it."""
    NOTE = "note"
    """Only adds `//` comment lines: a question or a note for the student."""
    FINAL_NEWLINE = "final_newline"
    """Adds the missing final newline to the data file."""


class NoteSection(StrEnum):
    """The PR section a note belongs in (docs/fix-pr-workflow.md §4); each note is in one."""

    NAMES = "names"
    """"Names (C# conventions)": a naming note."""
    FOR_YOU = "for_you"
    """"For you to do": a question or an instruction for the student."""


class PlannedEdit(DomainModel):
    """One search-and-replace edit, as the model planned it."""

    file: str
    """The file, relative to the repo root."""
    find: str
    """Text that occurs exactly once in the file."""
    replace: str
    kind: EditKind
    finding_ids: tuple[str, ...]
    """The requirements the edit addresses; empty for a naming note or a question."""
    reason: str
    fallback_note: str | None
    """For a `fix`: the comment to keep instead if the fix is dropped."""
    note_section: NoteSection | None
    """For a `note`: the PR section it belongs in; None for other kinds."""


class FixPlan(DomainModel):
    """The model's plan for one repo: the edits, and the PR's "Files" items."""

    edits: tuple[PlannedEdit, ...]
    files_notes: tuple[str, ...]
    """Leftover or duplicate files to mention in the PR, without changing them."""


class RejectedEdit(DomainModel):
    """An edit that was not applied, and why (fed back to the next attempt)."""

    edit: PlannedEdit
    reason: NonBlankStr


class EditApplication(DomainModel):
    """The result of applying a plan to the fix working copy."""

    applied: tuple[PlannedEdit, ...] = ()
    rejected: tuple[RejectedEdit, ...] = ()
    changed_files: tuple[str, ...] = ()
    ignored_review: tuple[str, ...] = ()
    """Self-review violations that were not acted on, and why (for the repo report)."""


class ReviewViolation(DomainModel):
    """A rule an applied edit breaks, found by the self-review."""

    edit_index: int
    """The edit's position in the list the review was given, from 0."""
    rule: int
    """The number of the review rule (docs/fix-pr-workflow.md §3) the edit breaks."""
    quote: str
    """The words of that rule the edit breaks, copied from the rule."""
    explanation: str


class SelfReview(DomainModel):
    """The self-review's reply: every violation it found; empty when the edits are fine."""

    violations: tuple[ReviewViolation, ...]


class PullRequestText(DomainModel):
    """The writer's reply: the PR body's opening and the items of its sections.

    The "Files" section is not the writer's: it is the plan's `files_notes`, verbatim.
    """

    opening: str
    """Starts with "Hi! "; one or two sentences on what works."""
    fixed: tuple[str, ...]
    """"Fixed in this PR (marked `//fix:` in the code)"."""
    for_you: tuple[str, ...]
    """"For you to do (marked in the code)"."""
    names: tuple[str, ...]
    """"Names (C# conventions)"."""


class VerifyResult(DomainModel):
    """Whether the fix working copy still builds and passes what it passed, and clears what
    its fixes claim."""

    passed: bool
    problems: tuple[str, ...] = ()
    failing_fixes: tuple[int, ...] = ()
    """Positions of the applied `fix` edits that verification blames, from 0."""
    cleared: tuple[str, ...] = ()
    """Requirements that failed before (had a finding) and pass on the fix working copy."""
    blocked: tuple[str, ...] = ()
    """Claims not held against their fix, because a requirement they depend on still fails."""


class FixAttempt(DomainModel):
    """One attempt's plan, the edits it kept and their recheck: what a proposal is made of."""

    number: int = Field(ge=1)
    plan: FixPlan
    application: EditApplication
    verification: VerifyResult


class LlmUsage(DomainModel):
    """The tokens of one LLM call, and the run that paid for it."""

    run_id: Identifier
    call_id: Identifier
    """Identifies the call, so a call replayed within its run is counted once."""
    role: Identifier
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class FixWorkspace(DomainModel):
    """The fix working copy of one repo, at the checked commit."""

    working_copy: Path
    base_sha: Identifier
    branch_name: NonBlankStr
    """The review branch, created when there is something to commit."""


class FixOutcome(DomainModel):
    """What the fixer did for one repo, for the repo report and the round summary."""

    repo_id: Identifier
    proposal_id: Identifier | None = None
    branch_name: NonBlankStr | None = None
    pr_title: NonBlankStr | None = None
    diff_path: Path | None = None
    attempts: int = Field(default=0, ge=0)
    edits: int = Field(default=0, ge=0)
    reason: str = ""
    """Why there is no proposal; empty when there is one."""
    ignored_review: tuple[str, ...] = ()
    """Self-review violations that were not acted on, and why."""
    attempt_choice: str = ""
    """Which attempt the proposal is made of, and why."""
    blocked: tuple[str, ...] = ()
    """Claims of the proposal's fixes that cannot pass until a requirement they depend on
    passes."""
