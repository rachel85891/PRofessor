"""Proposal models: targeted fixes, the proposal awaiting review, and the review decision."""

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr, RepoRelativePath
from hw_checker.domain.roster import RepoTarget


class Patch(DomainModel):
    """A targeted search-and-replace in one file, fixing one requirement (design §11)."""

    file_path: RepoRelativePath
    search: NonBlankStr
    replace: str
    requirement_id: Identifier
    explanation: NonBlankStr

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.search == self.replace:
            raise ValueError("replace must differ from search")
        return self


class ProposalStatus(StrEnum):
    """Lifecycle of a fix proposal (design §11). Only approved proposals are published."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class FixProposal(DomainModel):
    """The fixes proposed for one repo run, saved by the graph and published after approval."""

    proposal_id: Identifier
    repo_run_id: Identifier
    repo: RepoTarget
    branch_name: NonBlankStr
    patches: tuple[Patch, ...] = ()
    diff: str = ""
    verified: bool
    status: ProposalStatus = ProposalStatus.PENDING_REVIEW


class ReviewOutcome(StrEnum):
    """The reviewer's decision on a repo run (design §5)."""

    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"


class ReviewDecision(DomainModel):
    """A reviewer's decision on one repo run, with optional notes."""

    repo_run_id: Identifier
    outcome: ReviewOutcome
    reviewer_notes: str = ""
