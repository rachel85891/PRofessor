"""SQLAlchemy table models for runs, repo runs, findings, proposals and review decisions.

Columns that are filtered or sorted on are real columns; nested value objects are stored as
JSON written with the domain models' own JSON serialization. Only portable types are used.
The schema itself is created by the Alembic migration, never by `create_all`.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ID_LENGTH = 200
ID_TYPE = String(ID_LENGTH)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UtcDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as naive UTC and returns them aware (UTC)."""

    impl = DateTime(timezone=False)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes cannot be stored")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(ID_TYPE, primary_key=True)
    assignment_id: Mapped[str] = mapped_column(ID_TYPE, index=True)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    assignment_versions: Mapped[list[dict[str, str]]] = mapped_column(JSON)


class RepoRunRow(Base):
    __tablename__ = "repo_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_run_id: Mapped[str] = mapped_column(ID_TYPE, unique=True)
    run_id: Mapped[str] = mapped_column(
        ID_TYPE, ForeignKey("runs.run_id", ondelete="CASCADE"), index=True
    )
    repo_id: Mapped[str] = mapped_column(ID_TYPE, index=True)
    repo: Mapped[dict[str, Any]] = mapped_column(JSON)
    commit_sha: Mapped[str | None] = mapped_column(ID_TYPE, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    execution: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    assignment_ids: Mapped[list[str]] = mapped_column(JSON)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    errors: Mapped[list[str]] = mapped_column(JSON)


class _FindingColumns:
    """Columns shared by raw and judged findings."""

    position: Mapped[int] = mapped_column(Integer)
    finding_id: Mapped[str] = mapped_column(ID_TYPE)
    requirement_id: Mapped[str] = mapped_column(ID_TYPE, index=True)
    assignment_id: Mapped[str] = mapped_column(ID_TYPE, index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    message_he: Mapped[str] = mapped_column(Text)
    message_en: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class FindingRow(_FindingColumns, Base):
    """A raw finding, as produced by checking one assignment in one repo run."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_run_id: Mapped[str] = mapped_column(
        ID_TYPE, ForeignKey("repo_runs.repo_run_id", ondelete="CASCADE"), index=True
    )
    result_assignment_id: Mapped[str] = mapped_column(ID_TYPE)


class JudgedFindingRow(_FindingColumns, Base):
    """A finding with the judge's verdict; rejected findings are kept for calibration."""

    __tablename__ = "judged_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_run_id: Mapped[str] = mapped_column(
        ID_TYPE, ForeignKey("repo_runs.repo_run_id", ondelete="CASCADE"), index=True
    )
    verdict: Mapped[str] = mapped_column(String(16), index=True)
    reason: Mapped[str] = mapped_column(Text)
    adjusted_severity: Mapped[str] = mapped_column(String(16))


class ProposalRow(Base):
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(ID_TYPE, unique=True)
    repo_run_id: Mapped[str] = mapped_column(
        ID_TYPE, ForeignKey("repo_runs.repo_run_id", ondelete="CASCADE"), index=True
    )
    repo: Mapped[dict[str, Any]] = mapped_column(JSON)
    branch_name: Mapped[str] = mapped_column(String(255))
    patches: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    diff: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(32), index=True)


class ReviewDecisionRow(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_run_id: Mapped[str] = mapped_column(
        ID_TYPE, ForeignKey("repo_runs.repo_run_id", ondelete="CASCADE"), index=True
    )
    outcome: Mapped[str] = mapped_column(String(16))
    reviewer_notes: Mapped[str] = mapped_column(Text)
