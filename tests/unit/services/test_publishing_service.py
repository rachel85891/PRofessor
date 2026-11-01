from datetime import UTC, datetime
from pathlib import Path

import pytest

from hw_checker.domain import (
    BranchHead,
    FixProposal,
    GateDecision,
    ProposalStatus,
    PublishOutcome,
    PublishRecord,
    ReviewOutcome,
    RunSummary,
)
from hw_checker.domain.errors import GitOperationError, PublishError, ReviewError
from hw_checker.services.publishing import PublishService
from tests.fakes.clock import FakeClock
from tests.fakes.code_host import ACCOUNT, FakeCodeHost
from tests.fakes.git import FakeGitClient
from tests.fakes.reporting import FakeReportWriter
from tests.fakes.storage import InMemoryStorage
from tests.unit.storage import builders

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
REPO_ID = "avi__api"
CHECKED = "2" * 40
MOVED = "9" * 40


class Setup:
    """A stored run with one proposal, and the publishing service over fakes."""

    def __init__(
        self,
        *,
        head: str = CHECKED,
        code_host_error: PublishError | None = None,
        push_error: GitOperationError | PublishError | None = None,
        commit_author_configured: bool = True,
    ) -> None:
        self.storage = InMemoryStorage()
        run = builders.full_run()
        self.storage.runs.save(run)
        self.repo_run_id = run.repo_runs[2].repo_run_id
        self.code_host = FakeCodeHost(
            heads={REPO_ID: BranchHead(name="main", sha=head)},
            errors={REPO_ID: code_host_error} if code_host_error else None,
        )
        self.git = FakeGitClient(push_error=push_error)
        self.writer = FakeReportWriter()
        self.service = PublishService(
            self.storage.proposals,
            self.code_host,
            self.git,
            self.writer,
            FakeClock(NOW),
            commit_author_configured,
        )

    def save(self, status: ProposalStatus, proposal_id: str = "p-1") -> FixProposal:
        proposal = builders.proposal(proposal_id, self.repo_run_id, status)
        self.storage.proposals.save(proposal)
        return proposal

    def stored(self, proposal_id: str = "p-1") -> FixProposal:
        return self.storage.proposals.get(proposal_id)

    def log(self) -> tuple[PublishRecord, ...]:
        return self.storage.proposals.list_publish_records(self.repo_run_id)


def test_an_approved_proposal_is_pushed_to_the_fork_and_opened_as_a_pull_request() -> None:
    setup = Setup()
    proposal = setup.save(ProposalStatus.APPROVED)

    record = setup.service.publish(proposal)

    push_url = f"https://github.test/{ACCOUNT}/check_avi.git"
    assert setup.git.pushed == [(Path("workspace/clones/avi__api"), proposal.branch_name, push_url)]
    [pull] = setup.code_host.pulls
    assert (pull.head, pull.base) == (f"{ACCOUNT}:{proposal.branch_name}", "main")
    assert (pull.title, pull.body) == (proposal.pr_title, proposal.pr_body)
    assert record == PublishRecord(
        repo_run_id=setup.repo_run_id,
        repo_id=REPO_ID,
        branch_name=proposal.branch_name,
        fork=f"{ACCOUNT}/check_avi",
        pr_url="https://github.test/avi/api/pull/1",
        pr_number=1,
        published_at=NOW,
        outcome=PublishOutcome.PUBLISHED,
    )
    assert setup.log() == (record,)
    assert setup.stored().status is ProposalStatus.PUBLISHED


def test_publishing_the_same_branch_again_updates_the_same_pull_request() -> None:
    setup = Setup()
    setup.service.publish(setup.save(ProposalStatus.APPROVED))

    again = setup.service.publish(setup.save(ProposalStatus.APPROVED, "p-2"))

    assert (again.outcome, again.pr_number) == (PublishOutcome.UPDATED, 1)
    assert [pull.created for pull in setup.code_host.pulls] == [True, False]
    assert setup.stored("p-2").status is ProposalStatus.PUBLISHED


def test_a_moved_default_branch_is_stale_and_nothing_is_pushed() -> None:
    setup = Setup(head=MOVED)

    record = setup.service.publish(setup.save(ProposalStatus.APPROVED))

    assert record.outcome is PublishOutcome.STALE
    assert record.message == (
        "avi/api moved from 2222222 to 9999999 since the check; nothing was pushed. Check it "
        "again in a new round."
    )
    assert (setup.code_host.forks, setup.git.pushed, setup.code_host.pulls) == ([], [], [])
    assert setup.stored().status is ProposalStatus.APPROVED
    assert setup.log() == (record,)


def test_a_failure_is_recorded_and_the_proposal_can_be_approved_again() -> None:
    setup = Setup(code_host_error=PublishError("GitHub POST /forks failed (HTTP 403): no"))

    record = setup.service.publish(setup.save(ProposalStatus.APPROVED))

    assert record.outcome is PublishOutcome.FAILED
    assert record.message == "GitHub POST /forks failed (HTTP 403): no"
    assert setup.git.pushed == []
    assert setup.stored().status is ProposalStatus.PENDING_REVIEW
    assert setup.log() == (record,)


def test_a_rejected_push_is_recorded_as_a_failure() -> None:
    setup = Setup(push_error=PublishError("git push failed: non-fast-forward"))

    record = setup.service.publish(setup.save(ProposalStatus.APPROVED))

    assert (record.outcome, record.message) == (
        PublishOutcome.FAILED,
        "git push failed: non-fast-forward",
    )
    assert setup.code_host.pulls == []


def test_publishing_refuses_to_run_without_the_commit_author() -> None:
    setup = Setup(commit_author_configured=False)

    record = setup.service.publish(setup.save(ProposalStatus.APPROVED))

    assert record.outcome is PublishOutcome.FAILED
    assert "HW_CHECKER_GIT__COMMIT_AUTHOR_NAME" in record.message
    assert (setup.code_host.forks, setup.git.pushed) == ([], [])


@pytest.mark.parametrize(
    "status", [ProposalStatus.PENDING_REVIEW, ProposalStatus.REJECTED, ProposalStatus.PUBLISHED]
)
def test_only_an_approved_proposal_is_published(status: ProposalStatus) -> None:
    setup = Setup()

    with pytest.raises(ReviewError, match="only an approved proposal is published"):
        setup.service.publish(setup.save(status))

    assert (setup.log(), setup.git.pushed) == ((), [])


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (ReviewOutcome.APPROVED, ProposalStatus.APPROVED),
        (ReviewOutcome.REJECTED, ProposalStatus.REJECTED),
    ],
)
def test_a_decision_is_recorded_and_moves_the_proposal(
    outcome: ReviewOutcome, status: ProposalStatus
) -> None:
    setup = Setup()
    setup.save(ProposalStatus.PENDING_REVIEW)

    decided = setup.service.decide(setup.repo_run_id, outcome, "Looks right")

    assert decided.status is status
    assert setup.stored().status is status
    decision = setup.storage.proposals.get_review_decision(setup.repo_run_id)
    assert decision is not None
    assert (decision.outcome, decision.reviewer_notes) == (outcome, "Looks right")


def test_a_decision_needs_a_proposal() -> None:
    setup = Setup()

    with pytest.raises(ReviewError, match="no proposal for run-1/avi__api"):
        setup.service.decide(setup.repo_run_id, ReviewOutcome.APPROVED)


def test_a_decided_proposal_cannot_be_decided_again() -> None:
    setup = Setup()
    setup.save(ProposalStatus.REJECTED)

    with pytest.raises(ReviewError, match="is rejected; it cannot become approved"):
        setup.service.decide(setup.repo_run_id, ReviewOutcome.APPROVED)

    assert setup.storage.proposals.get_review_decision(setup.repo_run_id) is None


def test_editing_at_the_gate_is_not_available_yet() -> None:
    setup = Setup()
    setup.save(ProposalStatus.PENDING_REVIEW)

    with pytest.raises(ReviewError, match="not available yet"):
        setup.service.decide(setup.repo_run_id, ReviewOutcome.EDITED)


def summary(setup: Setup) -> RunSummary:
    return RunSummary.from_run(
        setup.storage.runs.get("run-1"),
        report_paths={REPO_ID: Path("reports/run-1/avi__api.md")},
    )


def test_nothing_pending_means_no_review_prompt() -> None:
    setup = Setup()
    setup.save(ProposalStatus.APPROVED)

    assert setup.service.review_prompt("run-1", summary(setup)) is None


def test_the_review_prompt_shows_counts_report_diff_and_the_last_attempt() -> None:
    setup = Setup()
    proposal = setup.save(ProposalStatus.PENDING_REVIEW)
    failed = builders.publish_record(setup.repo_run_id, PublishOutcome.FAILED)
    setup.storage.proposals.save_publish_record(failed)

    prompt = setup.service.review_prompt("run-1", summary(setup), "Try again")

    assert prompt is not None
    assert (prompt.run_id, prompt.message) == ("run-1", "Try again")
    [review] = prompt.pending
    assert (review.repo_id, review.repo_run_id) == (REPO_ID, setup.repo_run_id)
    assert (review.student, review.major) == (proposal.repo.student.name, 1)
    assert review.report_path == Path("reports/run-1/avi__api.md")
    assert review.diff_path == Path("reports/run-1/avi__api.diff")
    assert setup.writer.diffs[("run-1", REPO_ID)] == proposal.diff
    assert review.last_attempt == failed


def test_an_empty_diff_is_not_written() -> None:
    setup = Setup()
    setup.storage.proposals.save(
        builders.proposal("p-1", setup.repo_run_id, ProposalStatus.PENDING_REVIEW).model_copy(
            update={"diff": ""}
        )
    )

    prompt = setup.service.review_prompt("run-1", summary(setup))

    assert prompt is not None and prompt.pending[0].diff_path is None
    assert setup.writer.diffs == {}


def test_apply_decision_approves_with_notes_and_returns_the_repo_runs_to_publish() -> None:
    setup = Setup()
    setup.save(ProposalStatus.PENDING_REVIEW)

    approved = setup.service.apply_decision(
        "run-1", GateDecision(approve=(REPO_ID, REPO_ID), notes={REPO_ID: "Fine"})
    )

    assert approved == (setup.repo_run_id,)
    assert setup.stored().status is ProposalStatus.APPROVED
    decision = setup.storage.proposals.get_review_decision(setup.repo_run_id)
    assert decision is not None and decision.reviewer_notes == "Fine"


@pytest.mark.parametrize(
    ("decision", "problem"),
    [
        (GateDecision(approve=("nobody__api",)), "not pending: nobody__api"),
        (
            GateDecision(approve=(REPO_ID,), decline=(REPO_ID,)),
            f"both approved and declined: {REPO_ID}",
        ),
        (
            GateDecision(decline=(REPO_ID,), notes={"other__api": "?"}),
            "a note for a repo that is neither approved nor declined: other__api",
        ),
    ],
    ids=["unknown", "both", "stray-note"],
)
def test_an_invalid_decision_records_nothing(decision: GateDecision, problem: str) -> None:
    setup = Setup()
    setup.save(ProposalStatus.PENDING_REVIEW)

    with pytest.raises(ReviewError) as raised:
        setup.service.apply_decision("run-1", decision)

    assert str(raised.value) == f"Nothing was recorded; {problem}. Pending: {REPO_ID}."
    assert setup.stored().status is ProposalStatus.PENDING_REVIEW
    assert setup.storage.proposals.get_review_decision(setup.repo_run_id) is None


def test_publish_approved_publishes_each_repo_run() -> None:
    setup = Setup()
    setup.save(ProposalStatus.APPROVED)

    [record] = setup.service.publish_approved((setup.repo_run_id,))

    assert record.outcome is PublishOutcome.PUBLISHED


def test_publish_approved_needs_a_proposal() -> None:
    setup = Setup()

    with pytest.raises(ReviewError, match="no proposal for run-1/avi__api to publish"):
        setup.service.publish_approved((setup.repo_run_id,))
