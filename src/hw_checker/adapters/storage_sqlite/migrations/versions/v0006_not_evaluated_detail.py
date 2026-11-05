"""What a not-evaluated reason is about, such as the paths of `windows_path` (stage 15).

Existing rows get NULL, read as no detail.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("not_evaluated") as batch:
        batch.add_column(sa.Column("detail", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("not_evaluated") as batch:
        batch.drop_column("detail")
