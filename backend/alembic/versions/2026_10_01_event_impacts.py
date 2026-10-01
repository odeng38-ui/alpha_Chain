"""add event impact candidates"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261001_event_impacts"
down_revision: Union[str, None] = "20261001_global_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_impact_candidate",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("industry_id", sa.String(50), nullable=False),
        sa.Column("exposure", sa.Float(), nullable=False),
        sa.Column("impact_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("version", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["event_id"], ["global_event.id"]),
        sa.ForeignKeyConstraint(["security_id"], ["security.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_impact_candidate_event_id", "event_impact_candidate", ["event_id"])
    op.create_index("ix_event_impact_candidate_security_id", "event_impact_candidate", ["security_id"])
    op.create_index("ix_event_impact_candidate_industry_id", "event_impact_candidate", ["industry_id"])
    op.create_index("uq_event_impact_candidate", "event_impact_candidate", ["event_id", "security_id", "version"], unique=True)
    op.create_index("ix_event_impact_rank", "event_impact_candidate", ["event_id", "version", "rank"])


def downgrade() -> None:
    op.drop_table("event_impact_candidate")