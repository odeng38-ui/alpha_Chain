"""stage 8 explainable alpha score snapshots"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260921_stage8"
down_revision: Union[str, None] = "20260921_stage6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("feature_snapshot") as batch:
        batch.add_column(sa.Column("formula", sa.String(500), nullable=False, server_default=""))
        batch.add_column(sa.Column("inputs", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
        batch.add_column(sa.Column("source_available_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("missing_reason", sa.String(200), nullable=True))
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
        batch.create_index("uq_feature_snapshot", ["security_id", "as_of_date", "feature_name", "feature_version"], unique=True)
    with op.batch_alter_table("score_snapshot") as batch:
        batch.add_column(sa.Column("weights", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
        batch.add_column(sa.Column("explanations", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
        batch.add_column(sa.Column("completeness", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("config_hash", sa.String(64), nullable=False, server_default=""))
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
        batch.create_index("uq_score_snapshot", ["security_id", "as_of_date", "horizon", "version"], unique=True)
    op.create_table(
        "alpha_weight_config",
        sa.Column("version", sa.String(20), primary_key=True),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("changed_by", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("alpha_weight_config")
    with op.batch_alter_table("score_snapshot") as batch:
        batch.drop_index("uq_score_snapshot")
        for column in ("created_at", "config_hash", "completeness", "explanations", "weights"):
            batch.drop_column(column)
    with op.batch_alter_table("feature_snapshot") as batch:
        batch.drop_index("uq_feature_snapshot")
        for column in ("created_at", "missing_reason", "source_available_at", "inputs", "formula"):
            batch.drop_column(column)
