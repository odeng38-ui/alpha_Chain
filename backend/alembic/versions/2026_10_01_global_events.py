"""add standardized global market events"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261001_global_events"
down_revision: Union[str, None] = "20260928_stage11_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "global_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_id", sa.String(250), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("origin_country", sa.String(2), nullable=False),
        sa.Column("event_kind", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("direction", sa.String(20), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("return_1d", sa.Float(), nullable=True),
        sa.Column("zscore_20d", sa.Float(), nullable=True),
        sa.Column("shock_score", sa.Float(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
    )
    op.create_index("ix_global_event_external_id", "global_event", ["external_id"])
    op.create_index("ix_global_event_event_kind", "global_event", ["event_kind"])
    op.create_index("ix_global_event_symbol", "global_event", ["symbol"])
    op.create_index("ix_global_event_occurred_at", "global_event", ["occurred_at"])
    op.create_index("ix_global_event_available_at", "global_event", ["available_at"])
    op.create_index("ix_global_event_occurred_kind", "global_event", ["occurred_at", "event_kind"])
    op.create_index("ix_global_event_symbol_occurred", "global_event", ["symbol", "occurred_at"])


def downgrade() -> None:
    op.drop_table("global_event")