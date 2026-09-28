"""stage 9 walk-forward backtest reports"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260922_stage9"
down_revision: Union[str, None] = "20260921_stage8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtest_run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("score_version", sa.String(20), nullable=False),
        sa.Column("horizon", sa.String(20), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("parameter_adjustments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("train_start", sa.Date(), nullable=True),
        sa.Column("train_end", sa.Date(), nullable=True),
        sa.Column("validation_start", sa.Date(), nullable=True),
        sa.Column("validation_end", sa.Date(), nullable=True),
        sa.Column("test_start", sa.Date(), nullable=True),
        sa.Column("test_end", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="COMPLETED"),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_backtest_run_dataset_hash", "backtest_run", ["dataset_hash"])


def downgrade() -> None:
    op.drop_index("ix_backtest_run_dataset_hash", table_name="backtest_run")
    op.drop_table("backtest_run")
