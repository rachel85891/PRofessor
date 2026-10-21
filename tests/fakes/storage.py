"""In-memory fakes for the storage ports, with the same semantics as the SQL repositories."""

import threading
from dataclasses import dataclass, field

from hw_checker.domain import (
    FixProposal,
    JudgedFinding,
    ProposalStatus,
    RepoRunResult,
    ReviewDecision,
    Run,
)
from hw_checker.domain.errors import NotFoundError, StorageError


@dataclass
class _Store:
    """Shared state; dicts keep first-insertion order, like the SQL surrogate keys."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    runs: dict[str, Run] = field(default_factory=dict)
    repo_runs: dict[str, tuple[str, RepoRunResult]] = field(default_factory=dict)
    judged: dict[str, tuple[JudgedFinding, ...]] = field(default_factory=dict)
    proposals: dict[str, FixProposal] = field(default_factory=dict)
    decisions: dict[str, list[ReviewDecision]] = field(default_factory=dict)

    def require_repo_run(self, repo_run_id: str) -> None:
        if repo_run_id not in self.repo_runs:
            raise NotFoundError(f"no repo run with id {repo_run_id!r}")

    def upsert_repo_run(self, run_id: str, repo_run: RepoRunResult) -> None:
        stored = self.repo_runs.get(repo_run.repo_run_id)
        if stored is not None and stored[0] != run_id:
            raise StorageError(f"repo run {repo_run.repo_run_id!r} belongs to another run")
        self.repo_runs[repo_run.repo_run_id] = (
            run_id,
            repo_run.model_copy(update={"judged_findings": ()}),
        )
        self.judged[repo_run.repo_run_id] = repo_run.judged_findings

    def load_run(self, run_id: str) -> Run:
        repo_runs = tuple(
            repo_run.model_copy(update={"judged_findings": self.judged.get(repo_run_id, ())})
            for repo_run_id, (owner, repo_run) in self.repo_runs.items()
            if owner == run_id
        )
        return self.runs[run_id].model_copy(update={"repo_runs": repo_runs})


class FakeRunRepository:
    """In-memory `RunRepository`."""

    def __init__(self, store: _Store) -> None:
        self._store = store

    def save(self, run: Run) -> None:
        with self._store.lock:
            self._store.runs[run.run_id] = run.model_copy(update={"repo_runs": ()})
            for repo_run in run.repo_runs:
                self._store.upsert_repo_run(run.run_id, repo_run)

    def save_repo_run(self, run_id: str, repo_run: RepoRunResult) -> None:
        with self._store.lock:
            if run_id not in self._store.runs:
                raise NotFoundError(f"no run with id {run_id!r}")
            self._store.upsert_repo_run(run_id, repo_run)

    def get(self, run_id: str) -> Run:
        with self._store.lock:
            if run_id not in self._store.runs:
                raise NotFoundError(f"no run with id {run_id!r}")
            return self._store.load_run(run_id)

    def list_runs(self) -> tuple[Run, ...]:
        with self._store.lock:
            runs = [self._store.load_run(run_id) for run_id in self._store.runs]
        return tuple(sorted(runs, key=lambda run: (-run.started_at.timestamp(), run.run_id)))


class FakeFindingRepository:
    """In-memory `FindingRepository` over the same judged findings as the runs."""

    def __init__(self, store: _Store) -> None:
        self._store = store

    def save_judged_findings(self, repo_run_id: str, findings: tuple[JudgedFinding, ...]) -> None:
        with self._store.lock:
            self._store.require_repo_run(repo_run_id)
            self._store.judged[repo_run_id] = findings

    def list_judged_findings(self, repo_run_id: str) -> tuple[JudgedFinding, ...]:
        with self._store.lock:
            return self._store.judged.get(repo_run_id, ())


class FakeProposalRepository:
    """In-memory `ProposalRepository`; review decisions are kept as history."""

    def __init__(self, store: _Store) -> None:
        self._store = store

    def save(self, proposal: FixProposal) -> None:
        with self._store.lock:
            self._store.require_repo_run(proposal.repo_run_id)
            self._store.proposals[proposal.proposal_id] = proposal

    def get(self, proposal_id: str) -> FixProposal:
        with self._store.lock:
            if proposal_id not in self._store.proposals:
                raise NotFoundError(f"no proposal with id {proposal_id!r}")
            return self._store.proposals[proposal_id]

    def list_by_status(self, status: ProposalStatus) -> tuple[FixProposal, ...]:
        with self._store.lock:
            return tuple(p for p in self._store.proposals.values() if p.status is status)

    def save_review_decision(self, decision: ReviewDecision) -> None:
        with self._store.lock:
            self._store.require_repo_run(decision.repo_run_id)
            self._store.decisions.setdefault(decision.repo_run_id, []).append(decision)

    def get_review_decision(self, repo_run_id: str) -> ReviewDecision | None:
        with self._store.lock:
            history = self._store.decisions.get(repo_run_id, [])
            return history[-1] if history else None


class InMemoryStorage:
    """The three fake repositories over one shared in-memory store."""

    def __init__(self) -> None:
        store = _Store()
        self.runs = FakeRunRepository(store)
        self.findings = FakeFindingRepository(store)
        self.proposals = FakeProposalRepository(store)
