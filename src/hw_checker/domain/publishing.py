"""Publishing models: the round's approval gate and the record of each publish (ADR 0018)."""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr
from hw_checker.domain.vcs import CheckedCommit


class PublishOutcome(StrEnum):
    """What one publish attempt did."""

    PUBLISHED = "published"
    """A new pull request was opened."""
    UPDATED = "updated"
    """The open pull request for the branch was updated."""
    STALE = "stale"
    """The student's default branch moved since the check; nothing was pushed."""
    FAILED = "failed"
    """The attempt failed; the message says why."""


class PublishRecord(DomainModel):
    """One attempt to publish a repo run's proposal, kept in the publish log."""

    repo_run_id: Identifier
    repo_id: Identifier
    branch_name: NonBlankStr
    fork: NonBlankStr | None = None
    """The fork the branch was pushed to (`owner/name`); None if it was not reached."""
    pr_url: NonBlankStr | None = None
    pr_number: int | None = Field(default=None, ge=1)
    published_at: AwareDatetime
    outcome: PublishOutcome
    message: str = ""

    @model_validator(mode="after")
    def _pull_request_when_published(self) -> Self:
        has_pull_request = self.pr_url is not None and self.pr_number is not None
        published = self.outcome in {PublishOutcome.PUBLISHED, PublishOutcome.UPDATED}
        if published != has_pull_request:
            raise ValueError("a published or updated record has a pull request; others have none")
        return self


class GateDecision(DomainModel):
    """The reviewer's answer at the approval gate, by repo id.

    Repos in neither list stay pending, and the gate asks again for them.
    """

    approve: tuple[Identifier, ...] = ()
    decline: tuple[Identifier, ...] = ()
    notes: dict[str, str] = Field(default_factory=dict)
    """A note for some of the approved or declined repos, by repo id."""


class PendingReview(DomainModel):
    """One repo waiting at the approval gate, with what the reviewer needs to decide."""

    repo_id: Identifier
    repo_run_id: Identifier
    student: NonBlankStr
    critical: int = Field(default=0, ge=0)
    major: int = Field(default=0, ge=0)
    minor: int = Field(default=0, ge=0)
    not_evaluated: int = Field(default=0, ge=0)
    branch_name: NonBlankStr
    pr_title: NonBlankStr
    report_path: Path | None = None
    diff_path: Path | None = None
    """The proposal's diff, written next to the reports; None when the diff is empty."""
    last_attempt: PublishRecord | None = None
    """The last publish attempt, when an earlier approval failed to publish."""
    checked_commit: CheckedCommit | None = None
    """How the checked commit (the pull request's base) was chosen, when `ends_when` did."""


class ReviewPrompt(DomainModel):
    """What the approval gate shows: the pending repos, how to answer, and any error."""

    run_id: Identifier
    pending: tuple[PendingReview, ...]
    message: str = ""
    """Why the last answer was not applied; empty when it was."""
    how_to_answer: NonBlankStr
