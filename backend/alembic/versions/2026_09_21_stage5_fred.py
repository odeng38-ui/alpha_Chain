"""stage 5 FRED macro series and vintage metadata"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260921_stage5"
down_revision: Union[str, None] = "20260921_stage4_account"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "macro_series",
        sa.Column("series_id", sa.String(50), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("frequency", sa.String(50), nullable=False),
        sa.Column("frequency_short", sa.String(10), nullable=False),
        sa.Column("units", sa.String(200), nullable=False),
        sa.Column("units_short", sa.String(50), nullable=False),
        sa.Column("seasonal_adjustment", sa.String(100), nullable=False),
        sa.Column("seasonal_adjustment_short", sa.String(20), nullable=False),
        sa.Column("last_updated", sa.DateTime(), nullable=True),
        sa.Column("metadata_hash", sa.String(64), nullable=False),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("macro_observation") as batch:
        batch.add_column(sa.Column("collected_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("is_initial_release", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE macro_observation SET collected_at = available_at WHERE collected_at IS NULL")
    with op.batch_alter_table("macro_observation") as batch:
        batch.alter_column("collected_at", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("macro_observation") as batch:
        batch.drop_column("is_initial_release")
        batch.drop_column("collected_at")
    op.drop_table("macro_series")
