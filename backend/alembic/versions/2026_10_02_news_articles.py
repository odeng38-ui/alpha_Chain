"""add raw news article collection"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_news_articles"
down_revision: Union[str, None] = "20261001_event_impacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "news_article",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("language", sa.String(50), nullable=True),
        sa.Column("source_country", sa.String(100), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("image_url", sa.String(2000), nullable=True),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("collected_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("external_id"),
    )
    op.create_index("ix_news_article_external_id", "news_article", ["external_id"])
    op.create_index("ix_news_article_source", "news_article", ["source"])
    op.create_index("ix_news_article_domain", "news_article", ["domain"])
    op.create_index("ix_news_article_source_country", "news_article", ["source_country"])
    op.create_index("ix_news_article_published_at", "news_article", ["published_at"])
    op.create_index("ix_news_article_published_source", "news_article", ["published_at", "source"])


def downgrade() -> None:
    op.drop_table("news_article")