"""add news candidate validation runs"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_news_validation"
down_revision: Union[str, None] = "20261004_news_stock_candidates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "news_candidate_validation_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_news_validation_status", "news_candidate_validation_run", ["status"])
    op.create_index("ix_news_validation_evaluated", "news_candidate_validation_run", ["evaluated_at"])


def downgrade() -> None:
    op.drop_table("news_candidate_validation_run")