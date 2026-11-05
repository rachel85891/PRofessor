"""The app's answers to failed requests, kept with each finding (stage 15).

Raw and judged findings gain `responses`; existing rows get NULL, read as no responses.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

FINDING_TABLES = ("findings", "judged_findings")


def upgrade() -> None:
    for table in FINDING_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("responses", sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in FINDING_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("responses")
