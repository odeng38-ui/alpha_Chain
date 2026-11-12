"""Add explicit relationship evidence verification; never promote legacy rows."""

import sqlalchemy as sa

from alembic import op

revision = "20261029_s2b4_evidence_status"
down_revision = "20261028_s2b2_event_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "relationship_evidence",
        sa.Column("verification_status", sa.String(20), nullable=True,
                  comment="NULL/proposed = unverified; verified requires explicit review"),
    )


def downgrade() -> None:
    op.drop_column("relationship_evidence", "verification_status")
