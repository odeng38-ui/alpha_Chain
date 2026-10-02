"""add news stock linkage candidates"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_news_stock_candidates"
down_revision: Union[str, None] = "20261003_news_classification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "news_stock_candidate",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("classification_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("industry_id", sa.String(50), nullable=False),
        sa.Column("expected_direction", sa.String(20), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("version", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["classification_id"], ["news_classification.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["security_id"], ["security.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_news_stock_candidate_classification", "news_stock_candidate", ["classification_id"])
    op.create_index("ix_news_stock_candidate_security", "news_stock_candidate", ["security_id"])
    op.create_index("ix_news_stock_candidate_industry", "news_stock_candidate", ["industry_id"])
    op.create_index("ix_news_stock_candidate_rank", "news_stock_candidate", ["classification_id", "version", "rank"])
    op.create_index("uq_news_stock_candidate_version", "news_stock_candidate", ["classification_id", "security_id", "version"], unique=True)


def downgrade() -> None:
    op.drop_table("news_stock_candidate")