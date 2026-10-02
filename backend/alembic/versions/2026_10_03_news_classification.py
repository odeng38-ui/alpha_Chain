"""add structured news classifications"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261003_news_classification"
down_revision: Union[str, None] = "20261002_news_articles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "news_classification",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("news_article_id", sa.Integer(), nullable=False),
        sa.Column("event_kind", sa.String(50), nullable=False),
        sa.Column("industries", sa.JSON(), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("matched_keywords", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.String(30), nullable=False),
        sa.Column("classified_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["news_article_id"], ["news_article.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_news_classification_article", "news_classification", ["news_article_id"])
    op.create_index("ix_news_classification_kind", "news_classification", ["event_kind"])
    op.create_index("ix_news_classification_direction", "news_classification", ["direction"])
    op.create_index("uq_news_classification_version", "news_classification", ["news_article_id", "version"], unique=True)


def downgrade() -> None:
    op.drop_table("news_classification")