"""SQL implementations of RunRepository, FindingRepository and ProposalRepository."""

from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from hw_checker.adapters.storage_sqlite import mapping
from hw_checker.adapters.storage_sqlite.database import Database
from hw_checker.adapters.storage_sqlite.schema import (
    FindingRow,
    JudgedFindingRow,
    ProposalRow,
    RepoRunRow,
    ReviewDecisionRow,
    RunRow,
)
from hw_checker.domain import (
    FixProposal,
    JudgedFinding,
    ProposalStatus,
    RepoRunResult,
    ReviewDecision,
    Run,
)
from hw_checker.domain.errors import NotFoundError, StorageError


@contextmanager
def _loading(what: str) -> Iterator[None]:
    """Stored data that no longer matches the domain models is a storage problem."""
    try:
        yield
    except ValidationError as error:
        raise StorageError(f"stored {what} is invalid: {error.error_count()} problems") from error


def _require_repo_run(session: Session, repo_run_id: str) -> None:
    exists = session.scalar(select(RepoRunRow.id).where(RepoRunRow.repo_run_id == repo_run_id))
    if exists is None:
        raise NotFoundError(f"no repo run with id {repo_run_id!r}")


class SqlRunRepository:
    """`RunRepository` over SQL. Repo runs keep the order in which they were first saved."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def save(self, run: Run) -> None:
        with self._database.session(f"save run {run.run_id}") as session:
            row = session.get(RunRow, run.run_id)
            if row is None:
                row = RunRow(run_id=run.run_id)
                session.add(row)
            mapping.fill_run_row(row, run)
            session.flush()
            for repo_run in run.repo_runs:
                _upsert_repo_run(session, run.run_id, repo_run)

    def save_repo_run(self, run_id: str, repo_run: RepoRunResult) -> None:
        with self._database.session(f"save repo run {repo_run.repo_run_id}") as session:
            if session.get(RunRow, run_id) is None:
                raise NotFoundError(f"no run with id {run_id!r}")
            _upsert_repo_run(session, run_id, repo_run)

    def get(self, run_id: str) -> Run:
        with self._database.session(f"load run {run_id}") as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise NotFoundError(f"no run with id {run_id!r}")
            return _load_run(session, row)

    def list_runs(self) -> tuple[Run, ...]:
        with self._database.session("list runs") as session:
            rows = session.scalars(
                select(RunRow).order_by(RunRow.started_at.desc(), RunRow.run_id)
            ).all()
            return tuple(_load_run(session, row) for row in rows)


def _upsert_repo_run(session: Session, run_id: str, repo_run: RepoRunResult) -> None:
    row = session.scalars(
        select(RepoRunRow).where(RepoRunRow.repo_run_id == repo_run.repo_run_id)
    ).one_or_none()
    if row is None:
        row = RepoRunRow(repo_run_id=repo_run.repo_run_id, run_id=run_id)
        session.add(row)
    elif row.run_id != run_id:
        raise StorageError(f"repo run {repo_run.repo_run_id!r} belongs to another run")
    mapping.fill_repo_run_row(row, repo_run)
    session.flush()
    session.execute(delete(FindingRow).where(FindingRow.repo_run_id == repo_run.repo_run_id))
    session.add_all(mapping.finding_rows(repo_run))
    _replace_judged(session, repo_run.repo_run_id, repo_run.judged_findings)


def _replace_judged(
    session: Session, repo_run_id: str, findings: tuple[JudgedFinding, ...]
) -> None:
    session.execute(delete(JudgedFindingRow).where(JudgedFindingRow.repo_run_id == repo_run_id))
    session.add_all(mapping.judged_rows(repo_run_id, findings))


def _load_run(session: Session, row: RunRow) -> Run:
    repo_rows = session.scalars(
        select(RepoRunRow).where(RepoRunRow.run_id == row.run_id).order_by(RepoRunRow.id)
    ).all()
    with _loading(f"run {row.run_id}"):
        repo_runs = tuple(_load_repo_run(session, repo_row) for repo_row in repo_rows)
        return mapping.run_from_row(row, repo_runs)


def _load_repo_run(session: Session, row: RepoRunRow) -> RepoRunResult:
    findings = session.scalars(
        select(FindingRow)
        .where(FindingRow.repo_run_id == row.repo_run_id)
        .order_by(FindingRow.position)
    ).all()
    return mapping.repo_run_from_rows(row, findings, _judged_rows(session, row.repo_run_id))


def _judged_rows(session: Session, repo_run_id: str) -> list[JudgedFindingRow]:
    return list(
        session.scalars(
            select(JudgedFindingRow)
            .where(JudgedFindingRow.repo_run_id == repo_run_id)
            .order_by(JudgedFindingRow.position)
        ).all()
    )


class SqlFindingRepository:
    """`FindingRepository` over the same judged-findings rows that repo runs use."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def save_judged_findings(self, repo_run_id: str, findings: tuple[JudgedFinding, ...]) -> None:
        with self._database.session(f"save judged findings of {repo_run_id}") as session:
            _require_repo_run(session, repo_run_id)
            _replace_judged(session, repo_run_id, findings)

    def list_judged_findings(self, repo_run_id: str) -> tuple[JudgedFinding, ...]:
        with self._database.session(f"load judged findings of {repo_run_id}") as session:
            rows = _judged_rows(session, repo_run_id)
            with _loading(f"judged findings of {repo_run_id}"):
                return tuple(mapping.judged_from_row(row) for row in rows)


class SqlProposalRepository:
    """`ProposalRepository` over SQL. Review decisions are kept as history."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def save(self, proposal: FixProposal) -> None:
        with self._database.session(f"save proposal {proposal.proposal_id}") as session:
            _require_repo_run(session, proposal.repo_run_id)
            row = session.scalars(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal.proposal_id)
            ).one_or_none()
            if row is None:
                row = ProposalRow(proposal_id=proposal.proposal_id)
                session.add(row)
            mapping.fill_proposal_row(row, proposal)

    def get(self, proposal_id: str) -> FixProposal:
        with self._database.session(f"load proposal {proposal_id}") as session:
            row = session.scalars(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            ).one_or_none()
            if row is None:
                raise NotFoundError(f"no proposal with id {proposal_id!r}")
            with _loading(f"proposal {proposal_id}"):
                return mapping.proposal_from_row(row)

    def list_by_status(self, status: ProposalStatus) -> tuple[FixProposal, ...]:
        with self._database.session(f"list {status.value} proposals") as session:
            rows = session.scalars(
                select(ProposalRow)
                .where(ProposalRow.status == status.value)
                .order_by(ProposalRow.id)
            ).all()
            with _loading("proposals"):
                return tuple(mapping.proposal_from_row(row) for row in rows)

    def save_review_decision(self, decision: ReviewDecision) -> None:
        with self._database.session(f"save review decision for {decision.repo_run_id}") as session:
            _require_repo_run(session, decision.repo_run_id)
            session.add(mapping.decision_row(decision))

    def get_review_decision(self, repo_run_id: str) -> ReviewDecision | None:
        with self._database.session(f"load review decision for {repo_run_id}") as session:
            row = session.scalars(
                select(ReviewDecisionRow)
                .where(ReviewDecisionRow.repo_run_id == repo_run_id)
                .order_by(ReviewDecisionRow.id.desc())
                .limit(1)
            ).one_or_none()
            if row is None:
                return None
            with _loading(f"review decision for {repo_run_id}"):
                return mapping.decision_from_row(row)


class SqlStorage:
    """The three SQL repositories over one database (one engine per process)."""

    def __init__(self, database_url: str) -> None:
        self.database = Database(database_url)
        self.runs = SqlRunRepository(self.database)
        self.findings = SqlFindingRepository(self.database)
        self.proposals = SqlProposalRepository(self.database)

    def close(self) -> None:
        """Close every pooled connection."""
        self.database.dispose()
