"""Storage ports: one repository per aggregate (design §6, §12)."""

from typing import Protocol

from hw_checker.domain import (
    FixProposal,
    JudgedFinding,
    ProposalStatus,
    RepoRunResult,
    ReviewDecision,
    Run,
)


class RunRepository(Protocol):
    """Stores rounds and the per-repo results within them."""

    def save(self, run: Run) -> None:
        """Insert `run`, or replace the stored run with the same `run_id`, with its repo runs.

        Raises:
            StorageError: the run cannot be stored.
        """
        ...

    def save_repo_run(self, run_id: str, repo_run: RepoRunResult) -> None:
        """Insert or replace one repo's result within the run `run_id`.

        Raises:
            NotFoundError: no run with `run_id` exists.
            StorageError: the result cannot be stored.
        """
        ...

    def get(self, run_id: str) -> Run:
        """Return the run `run_id` with its repo runs.

        Raises:
            NotFoundError: no run with `run_id` exists.
            StorageError: the run cannot be loaded.
        """
        ...

    def list_runs(self) -> tuple[Run, ...]:
        """Return all runs, most recently started first.

        Raises:
            StorageError: the runs cannot be loaded.
        """
        ...


class FindingRepository(Protocol):
    """Stores judged findings, including rejected ones kept for calibration."""

    def save_judged_findings(self, repo_run_id: str, findings: tuple[JudgedFinding, ...]) -> None:
        """Replace the judged findings stored for `repo_run_id` with `findings`.

        Raises:
            StorageError: the findings cannot be stored.
        """
        ...

    def list_judged_findings(self, repo_run_id: str) -> tuple[JudgedFinding, ...]:
        """Return the judged findings stored for `repo_run_id` (empty if there are none).

        Raises:
            StorageError: the findings cannot be loaded.
        """
        ...


class ProposalRepository(Protocol):
    """Stores fix proposals and the reviewer's decisions on them."""

    def save(self, proposal: FixProposal) -> None:
        """Insert `proposal`, or replace the stored proposal with the same `proposal_id`.

        Raises:
            StorageError: the proposal cannot be stored.
        """
        ...

    def get(self, proposal_id: str) -> FixProposal:
        """Return the proposal `proposal_id`.

        Raises:
            NotFoundError: no proposal with `proposal_id` exists.
            StorageError: the proposal cannot be loaded.
        """
        ...

    def list_by_status(self, status: ProposalStatus) -> tuple[FixProposal, ...]:
        """Return all proposals with `status`.

        Raises:
            StorageError: the proposals cannot be loaded.
        """
        ...

    def save_review_decision(self, decision: ReviewDecision) -> None:
        """Insert or replace the review decision for `decision.repo_run_id`.

        Raises:
            StorageError: the decision cannot be stored.
        """
        ...

    def get_review_decision(self, repo_run_id: str) -> ReviewDecision | None:
        """Return the review decision for `repo_run_id`, or None if there is none yet.

        Raises:
            StorageError: the decision cannot be loaded.
        """
        ...
