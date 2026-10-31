"""Publishing service: records the reviewer's decisions and publishes approved proposals.

Deterministic, no LLM (ADR 0018). Each approved proposal is pushed to the publishing
account's fork of the student repo and opened as a pull request on the student's repo
(docs/fix-pr-workflow.md §2.6).
"""

import logging

from hw_checker.domain import (
    FixProposal,
    ProposalStatus,
    PublishOutcome,
    PublishRecord,
    ReviewDecision,
    ReviewOutcome,
)
from hw_checker.domain.errors import HwCheckerError, PublishError, ReviewError
from hw_checker.ports import Clock, CodeHost, GitClient, ProposalRepository

logger = logging.getLogger(__name__)

SHORT_SHA = 7
_STATUS_AFTER = {
    PublishOutcome.PUBLISHED: ProposalStatus.PUBLISHED,
    PublishOutcome.UPDATED: ProposalStatus.PUBLISHED,
    PublishOutcome.FAILED: ProposalStatus.PENDING_REVIEW,
}
"""A proposal's status after a publish attempt; a stale one stays approved and is not retried."""


class PublishService:
    """Publishes approved proposals and records the reviewer's decisions on proposals.

    `commit_author_configured` says whether the publishing account's commit author is set;
    publishing refuses to run without it, so no fix commit carries a personal identity.
    """

    def __init__(
        self,
        proposals: ProposalRepository,
        code_host: CodeHost,
        git: GitClient,
        clock: Clock,
        commit_author_configured: bool,
    ) -> None:
        self._proposals = proposals
        self._code_host = code_host
        self._git = git
        self._clock = clock
        self._commit_author_configured = commit_author_configured

    def publish(self, proposal: FixProposal) -> PublishRecord:
        """Publish an approved proposal, record the attempt and return the record.

        If the student's default branch moved since the check, nothing is pushed (`stale`).
        Otherwise the fork is ensured, the branch pushed and the pull request opened
        (`published`) or updated (`updated`). Any failure on the way is recorded as
        `failed` and moves the proposal back to pending review, so it can be approved again.

        Raises:
            ReviewError: the proposal is not approved.
            StorageError: the record or the proposal's new status cannot be saved.
        """
        if proposal.status is not ProposalStatus.APPROVED:
            raise ReviewError(
                f"The proposal for {proposal.repo.repo_id} is {proposal.status.value}; only "
                "an approved proposal is published."
            )
        try:
            record = self._publish(proposal)
        except HwCheckerError as error:
            record = self._record(proposal, PublishOutcome.FAILED, message=str(error))
        self._proposals.save_publish_record(record)
        status = _STATUS_AFTER.get(record.outcome)
        if status is not None:
            self._proposals.save(proposal.moved_to(status))
        log = logger.warning if record.outcome is PublishOutcome.FAILED else logger.info
        log(
            "Publishing %s: %s%s",
            proposal.repo.repo_id,
            record.outcome.value,
            f" ({record.pr_url})" if record.pr_url else f": {record.message}",
            extra={"repo_id": proposal.repo.repo_id, "outcome": record.outcome.value},
        )
        return record

    def decide(self, repo_run_id: str, outcome: ReviewOutcome, notes: str = "") -> FixProposal:
        """Record the reviewer's decision on the proposal of `repo_run_id` and return the
        proposal in its new status (approved or rejected).

        Raises:
            ReviewError: the repo run has no proposal, the proposal is not pending review,
                or the outcome is `edited` (editing at the gate is not available yet).
            StorageError: the decision cannot be saved.
        """
        proposal = self._proposals.get_for_repo_run(repo_run_id)
        if proposal is None:
            raise ReviewError(f"There is no proposal for {repo_run_id} to decide on.")
        if outcome is ReviewOutcome.EDITED:
            raise ReviewError("Editing a proposal at the approval gate is not available yet.")
        status = (
            ProposalStatus.APPROVED
            if outcome is ReviewOutcome.APPROVED
            else ProposalStatus.REJECTED
        )
        decided = proposal.moved_to(status)
        self._proposals.save_review_decision(
            ReviewDecision(repo_run_id=repo_run_id, outcome=outcome, reviewer_notes=notes)
        )
        self._proposals.save(decided)
        logger.info(
            "Review decision for %s: %s",
            proposal.repo.repo_id,
            outcome.value,
            extra={"repo_id": proposal.repo.repo_id, "outcome": outcome.value},
        )
        return decided

    def _publish(self, proposal: FixProposal) -> PublishRecord:
        if not self._commit_author_configured:
            raise PublishError(
                "The commit author is not set. Add HW_CHECKER_GIT__COMMIT_AUTHOR_NAME and "
                "HW_CHECKER_GIT__COMMIT_AUTHOR_EMAIL (the publishing account's no-reply "
                "address) to .env."
            )
        repo = proposal.repo
        head = self._code_host.default_branch_head(repo)
        if head.sha != proposal.base_sha:
            return self._record(
                proposal,
                PublishOutcome.STALE,
                message=(
                    f"{repo.owner}/{repo.name} moved from {proposal.base_sha[:SHORT_SHA]} to "
                    f"{head.sha[:SHORT_SHA]} since the check; nothing was pushed. Check it "
                    "again in a new round."
                ),
            )
        fork = self._code_host.ensure_fork(repo)
        self._git.push(proposal.local_repo_dir, proposal.branch_name, fork.push_url)
        pull = self._code_host.open_or_update_pull_request(
            repo, fork, proposal.branch_name, head.name, proposal.pr_title, proposal.pr_body
        )
        return self._record(
            proposal,
            PublishOutcome.PUBLISHED if pull.created else PublishOutcome.UPDATED,
            fork=fork.full_name,
            pr_url=pull.url,
            pr_number=pull.number,
        )

    def _record(
        self,
        proposal: FixProposal,
        outcome: PublishOutcome,
        *,
        message: str = "",
        fork: str | None = None,
        pr_url: str | None = None,
        pr_number: int | None = None,
    ) -> PublishRecord:
        return PublishRecord(
            repo_run_id=proposal.repo_run_id,
            repo_id=proposal.repo.repo_id,
            branch_name=proposal.branch_name,
            fork=fork,
            pr_url=pr_url,
            pr_number=pr_number,
            published_at=self._clock.now(),
            outcome=outcome,
            message=message,
        )
