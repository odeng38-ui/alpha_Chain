"""stage 6 relationship evidence and review fields"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260921_stage6"
down_revision: Union[str, None] = "20260921_stage5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("relationship") as batch:
        batch.add_column(sa.Column("extractor_version", sa.String(50), nullable=False, server_default="rule_v1"))
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("reviewed_by", sa.String(100), nullable=True))
        batch.create_index("uq_relationship_edge", ["source_id", "target_id", "type"], unique=True)
    with op.batch_alter_table("relationship_evidence") as batch:
        batch.add_column(sa.Column("source_document", sa.String(500), nullable=True))
        batch.add_column(sa.Column("source_url", sa.String(1000), nullable=True))
        batch.add_column(sa.Column("confidence", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("evidence_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.execute("UPDATE relationship_evidence SET source_document = COALESCE(filing_id, 'legacy'), evidence_hash = md5(relationship_id::text || excerpt || COALESCE(location, ''))")
    with op.batch_alter_table("relationship_evidence") as batch:
        batch.alter_column("source_document", nullable=False)
        batch.alter_column("evidence_hash", nullable=False)
        batch.create_index("uq_relationship_evidence_hash", ["relationship_id", "evidence_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("relationship_evidence") as batch:
        batch.drop_index("uq_relationship_evidence_hash")
        for column in ("created_at", "evidence_hash", "confidence", "source_url", "source_document"):
            batch.drop_column(column)
    with op.batch_alter_table("relationship") as batch:
        batch.drop_index("uq_relationship_edge")
        for column in ("reviewed_by", "reviewed_at", "created_at", "extractor_version"):
            batch.drop_column(column)
