"""Add EF-IMPACT-14 event_staging_item and event_cycle_history tables.

Revision ID: 20261111_ef14_event_staging
Revises: 20261029_s2b4_evidence_status
Create Date: 2026-11-11

Safety Notes:
- Connects directly to Neon production current head (20261029_s2b4_evidence_status)
  to avoid applying unrelated reviewer/verification model revisions.
- Run specifically via: `alembic upgrade 20261111_ef14_event_staging`
- WARNING: downgrade() drops staging and cycle history tables (DATA LOSS RISK).
  Automatic downgrade in production is strictly blocked unless explicitly overridden.
- NO CASCADE is used in downgrade statements.
"""
from __future__ import annotations

import os
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261111_ef14_event_staging"
down_revision = "20261029_s2b4_evidence_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "event_staging_item" not in existing_tables:
        op.create_table(
            "event_staging_item",
            sa.Column("rcept_no", sa.String(20), primary_key=True),
            sa.Column("company_name", sa.String(255), nullable=False),
            sa.Column("corp_code", sa.String(8), nullable=True),
            sa.Column("company_id", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "event_type",
                sa.String(50),
                nullable=False,
                server_default=sa.text("'CORPORATE_FILING'"),
            ),
            sa.Column(
                "priority_score",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("60"),
            ),
            sa.Column(
                "is_actionable",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "is_correction",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "document_version",
                sa.String(10),
                nullable=False,
                server_default=sa.text("'1'"),
            ),
            sa.Column("correction_of", sa.String(20), nullable=True),
            sa.Column(
                "status",
                sa.String(30),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by_worker", sa.String(64), nullable=True),
            sa.Column(
                "retry_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending', 'processing', 'processed', 'skipped_non_actionable', 'failed', 'quarantined')",
                name="chk_event_staging_status",
            ),
        )
        op.create_index(
            "idx_event_staging_item_status_order",
            "event_staging_item",
            ["status", "published_at", "rcept_no"],
        )
        op.create_index(
            "idx_event_staging_item_processing_lease",
            "event_staging_item",
            ["status", "processing_started_at"],
        )
        op.create_index(
            "idx_event_staging_item_corp_code",
            "event_staging_item",
            ["corp_code", "published_at"],
        )

    if "event_cycle_history" not in existing_tables:
        json_type = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
        op.create_table(
            "event_cycle_history",
            sa.Column(
                "id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("cycle_id", sa.String(64), nullable=False),
            sa.Column(
                "provider_id",
                sa.String(30),
                nullable=False,
                server_default=sa.text("'DART'"),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column(
                "dry_run",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("poll_status", sa.String(30), nullable=True),
            sa.Column("poll_range_start", sa.String(30), nullable=True),
            sa.Column("poll_range_end", sa.String(30), nullable=True),
            sa.Column(
                "last_polled_page",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
            sa.Column(
                "total_pages_detected",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
            sa.Column(
                "is_page_complete",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("high_water_mark_before", sa.String(20), nullable=True),
            sa.Column("high_water_mark_after", sa.String(20), nullable=True),
            sa.Column(
                "cursor_advanced",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "items_discovered",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "items_processed",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "items_failed",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "items_skipped",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "api_calls_made",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("metadata", json_type, nullable=True),
            sa.UniqueConstraint("cycle_id", name="event_cycle_history_cycle_id_key"),
            sa.CheckConstraint(
                "status IN ('running', 'completed', 'locked', 'failed')",
                name="chk_event_cycle_history_status",
            ),
        )
        if bind.dialect.name == "postgresql":
            op.create_index(
                "idx_event_cycle_history_started_at",
                "event_cycle_history",
                [sa.text("started_at DESC")],
            )
        else:
            op.create_index(
                "idx_event_cycle_history_started_at",
                "event_cycle_history",
                ["started_at"],
            )
        op.create_index(
            "idx_event_cycle_history_poll_scope",
            "event_cycle_history",
            ["poll_status", "is_page_complete"],
        )


def downgrade() -> None:
    """WARNING: Destructive operation (DATA LOSS RISK).

    Dropping event_staging_item and event_cycle_history erases all checkpoint
    watermarks, review holds, and cycle audit history.
    Blocked in production unless ALLOW_DESTRUCTIVE_DOWNGRADE=true is explicitly set.
    Never uses CASCADE.
    """
    app_env = (os.environ.get("APP_ENV") or "").lower()
    allow_destructive = (os.environ.get("ALLOW_DESTRUCTIVE_DOWNGRADE") or "").lower() == "true"
    if app_env == "production" and not allow_destructive:
        raise RuntimeError(
            "Destructive downgrade is blocked in production due to data loss risk. "
            "Set ALLOW_DESTRUCTIVE_DOWNGRADE=true only with explicit operational approval."
        )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "event_cycle_history" in existing_tables:
        existing_idx = {idx["name"] for idx in inspector.get_indexes("event_cycle_history")}
        if "idx_event_cycle_history_poll_scope" in existing_idx:
            op.drop_index("idx_event_cycle_history_poll_scope", table_name="event_cycle_history")
        if "idx_event_cycle_history_started_at" in existing_idx:
            op.drop_index("idx_event_cycle_history_started_at", table_name="event_cycle_history")
        op.drop_table("event_cycle_history")

    if "event_staging_item" in existing_tables:
        existing_idx = {idx["name"] for idx in inspector.get_indexes("event_staging_item")}
        if "idx_event_staging_item_corp_code" in existing_idx:
            op.drop_index("idx_event_staging_item_corp_code", table_name="event_staging_item")
        if "idx_event_staging_item_processing_lease" in existing_idx:
            op.drop_index("idx_event_staging_item_processing_lease", table_name="event_staging_item")
        if "idx_event_staging_item_status_order" in existing_idx:
            op.drop_index("idx_event_staging_item_status_order", table_name="event_staging_item")
        op.drop_table("event_staging_item")

