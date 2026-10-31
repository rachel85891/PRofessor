"""Publishing models: the record of one attempt to publish a repo's proposal (ADR 0018)."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr


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
