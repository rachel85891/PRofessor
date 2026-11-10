"""Publishing service: records the reviewer's decisions and publishes approved proposals.

Deterministic, no LLM (ADR 0018). Each approved proposal is pushed to the publishing
account's fork of the student repo and opened as a pull request on the student's repo
(docs/fix-pr-workflow.md §2.6).
"""

import logging

from hw_checker.domain import (
    FixProposal,
    GateDecision,
    PendingReview,
    ProposalStatus,
    PublishOutcome,
    PublishRecord,
    RepoSummary,
    ReviewDecision,
    ReviewOutcome,
    ReviewPrompt,
    RunSummary,
)
from hw_checker.domain.errors import HwCheckerError, PublishError, ReviewError
from hw_checker.ports import Clock, CodeHost, GitClient, ProposalRepository, ReportWriter

logger = logging.getLogger(__name__)

SHORT_SHA = 7
_STATUS_AFTER = {
    PublishOutcome.PUBLISHED: ProposalStatus.PUBLISHED,
    PublishOutcome.UPDATED: ProposalStatus.PUBLISHED,
    PublishOutcome.FAILED: ProposalStatus.PENDING_REVIEW,
}
"""A proposal's status after a publish attempt; a stale one stays approved and is not retried."""
HOW_TO_ANSWER = (
    'Resume with {"approve": [repo ids], "decline": [repo ids], "notes": {repo id: text}}. '
    "Approved repos are published; repos not mentioned stay pending."
)


class PublishService:
    """The round's approval gate and publishing: what is pending, the reviewer's decisions,
    and publishing approved proposals.

    `commit_author_configured` says whether the publishing account's commit author is set;
    publishing refuses to run without it, so no fix commit carries a personal identity.
    """

    def __init__(
        self,
        proposals: ProposalRepository,
        code_host: CodeHost,
        git: GitClient,
        writer: ReportWriter,
        clock: Clock,
        commit_author_configured: bool,
    ) -> None:
        self._proposals = proposals
        self._code_host = code_host
        self._git = git
        self._writer = writer
        self._clock = clock
        self._commit_author_configured = commit_author_configured

    def review_prompt(
        self, run_id: str, summary: RunSummary, message: str = ""
    ) -> ReviewPrompt | None:
        """What the approval gate shows for the run `run_id`, or None if nothing is pending.

        The pending repos follow the summary's (roster) order, except that repos where an
        assignment's `applies_when` or `skip_when` could not be evaluated come first; every
        such repo of the round is also listed on its own. Each comes with its counts
        and report from `summary`, its branch and
        pull request title, its diff (written next to the reports) and, after a failed
        publish, that attempt. `message` explains why the last answer was not applied.

        Raises:
            StorageError: the proposals cannot be loaded or a diff cannot be written.
        """
        lines = {line.repo_id: line for line in summary.repos}
        unknown = tuple(item for line in summary.repos for item in line.unknown_conditions)
        first = {item.repo_id for item in unknown}
        order = {repo_id: (repo_id not in first, index) for index, repo_id in enumerate(lines)}
        proposals = sorted(
            (
                p
                for p in self._proposals.list_for_run(run_id)
                if p.status is ProposalStatus.PENDING_REVIEW
            ),
            key=lambda proposal: order.get(proposal.repo.repo_id, (True, len(order))),
        )
        pending = tuple(
            self._pending_review(run_id, proposal, lines.get(proposal.repo.repo_id))
            for proposal in proposals
        )
        if not pending:
            return None
        return ReviewPrompt(
            run_id=run_id,
            unknown_conditions=unknown,
            pending=pending,
            message=message,
            how_to_answer=HOW_TO_ANSWER,
        )

    def apply_decision(self, run_id: str, decision: GateDecision) -> tuple[str, ...]:
        """Record the reviewer's answer at the gate and return the approved repo run ids.

        Nothing is recorded unless the whole answer is valid.

        Raises:
            ReviewError: a repo id is not pending in the run, a repo is both approved and
                declined, or a note is for a repo the answer does not mention.
            StorageError: a decision cannot be saved.
        """
        pending = {
            proposal.repo.repo_id: proposal
            for proposal in self._proposals.list_for_run(run_id)
            if proposal.status is ProposalStatus.PENDING_REVIEW
        }
        approve, decline = _unique(decision.approve), _unique(decision.decline)
        _check_decision(approve, decline, decision.notes, pending)
        for repo_id, outcome in [(r, ReviewOutcome.APPROVED) for r in approve] + [
            (r, ReviewOutcome.REJECTED) for r in decline
        ]:
            self.decide(pending[repo_id].repo_run_id, outcome, decision.notes.get(repo_id, ""))
        return tuple(pending[repo_id].repo_run_id for repo_id in approve)

    def publish_approved(self, repo_run_ids: tuple[str, ...]) -> tuple[PublishRecord, ...]:
        """Publish the approved proposal of each repo run, one after another.

        Raises:
            ReviewError: a repo run has no approved proposal.
            StorageError: a record or a proposal's status cannot be saved.
        """
        records = []
        for repo_run_id in repo_run_ids:
            proposal = self._proposals.get_for_repo_run(repo_run_id)
            if proposal is None:
                raise ReviewError(f"There is no proposal for {repo_run_id} to publish.")
            records.append(self.publish(proposal))
        return tuple(records)

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

    def _pending_review(
        self, run_id: str, proposal: FixProposal, line: RepoSummary | None
    ) -> PendingReview:
        repo_id = proposal.repo.repo_id
        attempts = self._proposals.list_publish_records(proposal.repo_run_id)
        return PendingReview(
            repo_id=repo_id,
            repo_run_id=proposal.repo_run_id,
            student=proposal.repo.student.name,
            critical=line.critical if line else 0,
            major=line.major if line else 0,
            minor=line.minor if line else 0,
            not_evaluated=line.not_evaluated if line else 0,
            branch_name=proposal.branch_name,
            pr_title=proposal.pr_title,
            report_path=line.report_path if line else None,
            diff_path=(
                self._writer.write_diff(run_id, repo_id, proposal.diff) if proposal.diff else None
            ),
            last_attempt=attempts[-1] if attempts else None,
            checked_commit=line.checked_commit if line else None,
        )

    def _publish(self, proposal: FixProposal) -> PublishRecord:
        if not self._commit_author_configured:
            raise PublishError(
                "The commit author is not set. Add HW_CHECKER_GIT__COMMIT_AUTHOR_NAME and "
                "HW_CHECKER_GIT__COMMIT_AUTHOR_EMAIL (the publishing account's no-reply "
                "address) to .env."
            )
        repo = proposal.repo
        head = self._code_host.default_branch_head(repo)
        # A proposal may be based on an earlier commit (`ends_when`); what must not have
        # moved is the branch head the round saw.
        if head.sha != proposal.expected_head:
            return self._record(
                proposal,
                PublishOutcome.STALE,
                message=(
                    f"{repo.owner}/{repo.name} moved from {proposal.expected_head[:SHORT_SHA]} to "
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


def _unique(repo_ids: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(repo_ids))


def _check_decision(
    approve: tuple[str, ...],
    decline: tuple[str, ...],
    notes: dict[str, str],
    pending: dict[str, FixProposal],
) -> None:
    problems = []
    unknown = [repo_id for repo_id in (*approve, *decline) if repo_id not in pending]
    if unknown:
        problems.append(f"not pending: {', '.join(unknown)}")
    both = [repo_id for repo_id in approve if repo_id in decline]
    if both:
        problems.append(f"both approved and declined: {', '.join(both)}")
    stray = [repo_id for repo_id in notes if repo_id not in (*approve, *decline)]
    if stray:
        problems.append(
            f"a note for a repo that is neither approved nor declined: {', '.join(stray)}"
        )
    if problems:
        raise ReviewError(
            f"Nothing was recorded; {'; '.join(problems)}. Pending: {', '.join(pending) or 'none'}."
        )
