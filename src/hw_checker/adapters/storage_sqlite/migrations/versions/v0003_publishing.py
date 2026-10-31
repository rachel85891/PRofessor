"""Publishing data on proposals, and the publish log (stage 13a).

Proposals gain the checked and branch commits, the local working copy and the pull
request's text. Nothing created proposals before this revision, so the empty values given
to existing rows are never read.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ID = sa.String(200)
PROPOSAL_COLUMNS = (
    ("base_sha", ID),
    ("head_sha", ID),
    ("local_repo_dir", sa.Text()),
    ("pr_title", sa.Text()),
    ("pr_body", sa.Text()),
)


def upgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        for name, column_type in PROPOSAL_COLUMNS:
            batch.add_column(sa.Column(name, column_type, nullable=False, server_default=""))
    # The default only fills existing rows; new rows always carry their own values.
    with op.batch_alter_table("proposals") as batch:
        for name, _ in PROPOSAL_COLUMNS:
            batch.alter_column(name, server_default=None)
    op.create_table(
        "publish_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("repo_run_id", ID, nullable=False),
        sa.Column("repo_id", ID, nullable=False),
        sa.Column("branch_name", sa.String(255), nullable=False),
        sa.Column("fork", sa.String(255), nullable=True),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_publish_log"),
        sa.ForeignKeyConstraint(
            ["repo_run_id"],
            ["repo_runs.repo_run_id"],
            name="fk_publish_log_repo_run_id_repo_runs",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_publish_log_repo_run_id", "publish_log", ["repo_run_id"])
    op.create_index("ix_publish_log_outcome", "publish_log", ["outcome"])


def downgrade() -> None:
    op.drop_table("publish_log")
    with op.batch_alter_table("proposals") as batch:
        for name, _ in reversed(PROPOSAL_COLUMNS):
            batch.drop_column(name)
