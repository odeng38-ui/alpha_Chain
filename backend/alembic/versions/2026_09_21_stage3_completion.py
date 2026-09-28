"""complete stage 2/3 history and checkpoint schema"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260921_stage3_complete"
down_revision: Union[str, None] = "20260921_stage3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("identifier_map") as batch:
        batch.add_column(sa.Column("effective_from", sa.Date(), nullable=True))
        batch.add_column(sa.Column("effective_to", sa.Date(), nullable=True))
    op.create_table(
        "collection_checkpoint",
        sa.Column("job_name", sa.String(50), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("last_success_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["security_id"], ["security.id"]),
        sa.PrimaryKeyConstraint("job_name", "security_id"),
    )


def downgrade() -> None:
    op.drop_table("collection_checkpoint")
    with op.batch_alter_table("identifier_map") as batch:
        batch.drop_column("effective_to")
        batch.drop_column("effective_from")
