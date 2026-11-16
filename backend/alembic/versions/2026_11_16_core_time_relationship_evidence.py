"""Make relationship_evidence.published_at nullable and add first_seen_at column.

Revision ID: 20261116_core_time_relationship_evidence
Revises: 20261111_ef14_event_staging
Create Date: 2026-11-16

Safety & Migration Notes:
- Backward compatibility: Existing records keep published_at intact; first_seen_at is NULL.
- SQLite & PostgreSQL compatibility: Uses op.batch_alter_table() to support SQLite table rebuilding
  as well as PostgreSQL native ALTER TABLE without exclusive lock issues.
- Existing row handling: No timestamps are guessed or retroactively created.
- Downgrade constraint: Altering published_at back to nullable=False will fail if any rows
  have published_at IS NULL. Automatic downgrade in production is blocked unless nulls are resolved.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261116_core_time_relationship_evidence"
down_revision = "20261111_ef14_event_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("relationship_evidence") as batch_op:
        batch_op.alter_column(
            "published_at",
            existing_type=sa.DateTime(),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "first_seen_at",
                sa.DateTime(),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("relationship_evidence") as batch_op:
        batch_op.drop_column("first_seen_at")
        batch_op.alter_column(
            "published_at",
            existing_type=sa.DateTime(),
            nullable=False,
        )

