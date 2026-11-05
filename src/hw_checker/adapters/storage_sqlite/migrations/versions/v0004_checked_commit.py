"""How the checked commit was chosen, and the branch head a proposal was made against.

Repo runs gain `checked_commit` (an assignment's `ends_when` chose an earlier commit, or
not); proposals gain `branch_head_sha`, the student's branch head at fetch time when it is
newer than the proposal's base. Existing rows get NULL: they checked the branch head.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ID = sa.String(200)


def upgrade() -> None:
    with op.batch_alter_table("repo_runs") as batch:
        batch.add_column(sa.Column("checked_commit", sa.JSON(), nullable=True))
    with op.batch_alter_table("proposals") as batch:
        batch.add_column(sa.Column("branch_head_sha", ID, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.drop_column("branch_head_sha")
    with op.batch_alter_table("repo_runs") as batch:
        batch.drop_column("checked_commit")
