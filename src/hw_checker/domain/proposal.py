"""Proposal models: targeted fixes, the proposal awaiting review, and the review decision."""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import model_validator

from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr, RepoRelativePath
from hw_checker.domain.errors import ReviewError
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
    """Lifecycle of a fix proposal (design §11). Only approved proposals are published.

    `pending_review → approved → published`, or `pending_review → rejected`. A publish that
    fails moves an approved proposal back to `pending_review`, so it can be approved again.
    """

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


_NEXT_STATUSES: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.PENDING_REVIEW: frozenset({ProposalStatus.APPROVED, ProposalStatus.REJECTED}),
    ProposalStatus.APPROVED: frozenset({ProposalStatus.PUBLISHED, ProposalStatus.PENDING_REVIEW}),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.PUBLISHED: frozenset(),
}


class FixProposal(DomainModel):
    """The fixes proposed for one repo run, saved by the graph and published after approval.

    The fixes are commits on `branch_name` in the local working copy `local_repo_dir`,
    from `base_sha` (the checked commit) to `head_sha`; `pr_title` and `pr_body` are the
    pull request's text (docs/fix-pr-workflow.md §4).
    """

    proposal_id: Identifier
    repo_run_id: Identifier
    repo: RepoTarget
    branch_name: NonBlankStr
    base_sha: Identifier
    head_sha: Identifier
    local_repo_dir: Path
    pr_title: NonBlankStr
    pr_body: NonBlankStr
    patches: tuple[Patch, ...] = ()
    diff: str = ""
    verified: bool
    status: ProposalStatus = ProposalStatus.PENDING_REVIEW

    def moved_to(self, status: ProposalStatus) -> Self:
        """Return this proposal in `status`.

        Raises:
            ReviewError: the lifecycle does not allow moving from the current status.
        """
        if status not in _NEXT_STATUSES[self.status]:
            raise ReviewError(
                f"The proposal for {self.repo.repo_id} is {self.status.value}; it cannot "
                f"become {status.value}."
            )
        return self.model_copy(update={"status": status})


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
