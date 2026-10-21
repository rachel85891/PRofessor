"""Storage ports: one repository per aggregate (design §6, §12).

Judged findings have one source of truth: their own store. `RepoRunResult.judged_findings`
is written to it and read from it, and `FindingRepository` works on the same rows.
"""

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
        """Upsert the run's own fields and every repo run it contains.

        Each contained repo run is upserted with its raw findings and its judged findings,
        which replace the ones stored for it. Repo runs that are stored but absent from
        `run.repo_runs` are kept, because a run may be saved before all its repos finish.

        Raises:
            StorageError: the run cannot be stored, for example a contained repo run id
                already belongs to another run.
        """
        ...

    def save_repo_run(self, run_id: str, repo_run: RepoRunResult) -> None:
        """Upsert one repo run, with its raw and judged findings, within the run `run_id`.

        Raises:
            NotFoundError: no run with `run_id` exists.
            StorageError: the result cannot be stored.
        """
        ...

    def get(self, run_id: str) -> Run:
        """Return the run `run_id` with all its stored repo runs, in the order first saved.

        Raises:
            NotFoundError: no run with `run_id` exists.
            StorageError: the run cannot be loaded.
        """
        ...

    def list_runs(self) -> tuple[Run, ...]:
        """Return all runs, newest `started_at` first.

        Raises:
            StorageError: the runs cannot be loaded.
        """
        ...


class FindingRepository(Protocol):
    """Stores judged findings, including rejected ones kept for calibration."""

    def save_judged_findings(self, repo_run_id: str, findings: tuple[JudgedFinding, ...]) -> None:
        """Replace exactly the judged findings stored for `repo_run_id` with `findings`.

        Accepted and rejected findings are stored alike, with their reason.

        Raises:
            NotFoundError: no repo run with `repo_run_id` exists.
            StorageError: the findings cannot be stored.
        """
        ...

    def list_judged_findings(self, repo_run_id: str) -> tuple[JudgedFinding, ...]:
        """Return the judged findings stored for `repo_run_id`, in saved order.

        Returns an empty tuple if there are none.

        Raises:
            StorageError: the findings cannot be loaded.
        """
        ...


class ProposalRepository(Protocol):
    """Stores fix proposals and the reviewer's decisions on them."""

    def save(self, proposal: FixProposal) -> None:
        """Insert `proposal`, or update the stored proposal with the same `proposal_id`.

        An update keeps the proposal's original creation order.

        Raises:
            NotFoundError: no repo run with `proposal.repo_run_id` exists.
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
        """Return all proposals with `status`, in the order they were first saved.

        Raises:
            StorageError: the proposals cannot be loaded.
        """
        ...

    def save_review_decision(self, decision: ReviewDecision) -> None:
        """Add `decision` for `decision.repo_run_id`, keeping earlier decisions as history.

        Raises:
            NotFoundError: no repo run with `decision.repo_run_id` exists.
            StorageError: the decision cannot be stored.
        """
        ...

    def get_review_decision(self, repo_run_id: str) -> ReviewDecision | None:
        """Return the latest review decision for `repo_run_id`, or None if there is none.

        Raises:
            StorageError: the decision cannot be loaded.
        """
        ...
