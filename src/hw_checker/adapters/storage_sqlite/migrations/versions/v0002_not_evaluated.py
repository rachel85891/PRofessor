"""Not-evaluated requirements of each assignment result, one row per entry.

Existing repo runs have no rows here, so they load with an empty `not_evaluated`.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ID = sa.String(200)


def upgrade() -> None:
    op.create_table(
        "not_evaluated",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("repo_run_id", ID, nullable=False),
        sa.Column("result_assignment_id", ID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("requirement_id", ID, nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_not_evaluated"),
        sa.ForeignKeyConstraint(
            ["repo_run_id"],
            ["repo_runs.repo_run_id"],
            name="fk_not_evaluated_repo_run_id_repo_runs",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_not_evaluated_repo_run_id", "not_evaluated", ["repo_run_id"])
    op.create_index("ix_not_evaluated_reason", "not_evaluated", ["reason"])


def downgrade() -> None:
    op.drop_table("not_evaluated")
