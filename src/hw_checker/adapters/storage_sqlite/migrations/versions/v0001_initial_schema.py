"""Initial schema: runs, repo runs, findings, judged findings, proposals, review decisions.

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""

from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ID = sa.String(200)
TABLES_IN_DROP_ORDER = (
    "review_decisions",
    "proposals",
    "judged_findings",
    "findings",
    "repo_runs",
    "runs",
)


def _finding_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("finding_id", ID, nullable=False),
        sa.Column("requirement_id", ID, nullable=False),
        sa.Column("assignment_id", ID, nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("message_he", sa.Text(), nullable=False),
        sa.Column("message_en", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
    ]


def _repo_run_foreign_key(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["repo_run_id"],
        ["repo_runs.repo_run_id"],
        name=f"fk_{table}_repo_run_id_repo_runs",
        ondelete="CASCADE",
    )


def _index(table: str, *columns: str) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", ID, nullable=False),
        sa.Column("assignment_id", ID, nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("assignment_versions", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", name="pk_runs"),
    )
    _index("runs", "assignment_id", "started_at", "status")

    op.create_table(
        "repo_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("repo_run_id", ID, nullable=False),
        sa.Column("run_id", ID, nullable=False),
        sa.Column("repo_id", ID, nullable=False),
        sa.Column("repo", sa.JSON(), nullable=False),
        sa.Column("commit_sha", ID, nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("execution", sa.JSON(), nullable=True),
        sa.Column("assignment_ids", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_repo_runs"),
        sa.UniqueConstraint("repo_run_id", name="uq_repo_runs_repo_run_id"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.run_id"], name="fk_repo_runs_run_id_runs", ondelete="CASCADE"
        ),
    )
    _index("repo_runs", "run_id", "repo_id", "status")

    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("repo_run_id", ID, nullable=False),
        sa.Column("result_assignment_id", ID, nullable=False),
        *_finding_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_findings"),
        _repo_run_foreign_key("findings"),
    )
    _index("findings", "repo_run_id", "requirement_id", "assignment_id", "severity", "source")

    op.create_table(
        "judged_findings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("repo_run_id", ID, nullable=False),
        *_finding_columns(),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("adjusted_severity", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_judged_findings"),
        _repo_run_foreign_key("judged_findings"),
    )
    _index(
        "judged_findings",
        "repo_run_id",
        "requirement_id",
        "assignment_id",
        "severity",
        "source",
        "verdict",
    )

    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("proposal_id", ID, nullable=False),
        sa.Column("repo_run_id", ID, nullable=False),
        sa.Column("repo", sa.JSON(), nullable=False),
        sa.Column("branch_name", sa.String(255), nullable=False),
        sa.Column("patches", sa.JSON(), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_proposals"),
        sa.UniqueConstraint("proposal_id", name="uq_proposals_proposal_id"),
        _repo_run_foreign_key("proposals"),
    )
    _index("proposals", "repo_run_id", "status")

    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("repo_run_id", ID, nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reviewer_notes", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_review_decisions"),
        _repo_run_foreign_key("review_decisions"),
    )
    _index("review_decisions", "repo_run_id")


def downgrade() -> None:
    for table in TABLES_IN_DROP_ORDER:
        op.drop_table(table)
