"""S2-B2: add GlobalEventEvidence and EventCompanyLink; extend GlobalEvent"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "20261028_s2b2_event_evidence"
down_revision: Union[str, None] = "20261028_global_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. GlobalEvent 최소 확장 (NULL 허용 — 기존 행 호환 유지)
    # ------------------------------------------------------------------ #
    op.add_column(
        "global_event",
        sa.Column(
            "verification_status",
            sa.String(20),
            nullable=True,
            comment="proposed | verified | rejected. NULL = legacy event (no promotion)",
        ),
    )
    op.add_column(
        "global_event",
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="system ingestion timestamp; distinct from occurred_at / available_at",
        ),
    )
    op.add_column(
        "global_event",
        sa.Column(
            "event_schema_version",
            sa.String(20),
            nullable=True,
            comment="schema version for structured event_metadata, e.g. s2b2-v1",
        ),
    )
    op.create_index(
        "ix_global_event_verification_status",
        "global_event",
        ["verification_status"],
    )

    # ------------------------------------------------------------------ #
    # 2. GlobalEventEvidence (신규 테이블)
    # ------------------------------------------------------------------ #
    op.create_table(
        "global_event_evidence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("global_event.id"),
            nullable=False,
        ),
        sa.Column("evidence_type", sa.String(20), nullable=False,
                  comment="OFFICIAL | REGULATORY | NEWS | ANALYSIS"),
        sa.Column("source_document", sa.String(500), nullable=True),
        sa.Column("source_url", sa.String(2000), nullable=True),
        sa.Column("location", sa.String(300), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        # timezone-aware datetime — PostgreSQL: TIMESTAMPTZ, SQLite: stored as text
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True,
                  comment="원문 최초 공개 시각(UTC). 불명확하면 NULL."),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False,
                  comment="시스템 수집 시각(UTC). published_at과 혼동 금지."),
        sa.Column("extracted_facts", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::json")),
        sa.Column("interpretations", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::json")),
        sa.Column("verification_status", sa.String(20), nullable=False,
                  server_default="proposed",
                  comment="proposed | verified | rejected"),
        sa.Column("evidence_hash", sa.String(64), nullable=False, unique=True,
                  comment="SHA-256(source_url + excerpt) 중복 방지"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_gee_event_status", "global_event_evidence",
                    ["event_id", "verification_status"])
    op.create_index("ix_gee_published_at", "global_event_evidence", ["published_at"])
    op.create_index("uq_gee_evidence_hash", "global_event_evidence",
                    ["evidence_hash"], unique=True)

    # ------------------------------------------------------------------ #
    # 3. EventCompanyLink (신규 테이블)
    # ------------------------------------------------------------------ #
    op.create_table(
        "event_company_link",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("global_event.id"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("company.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(30), nullable=False,
                  comment="ANCHOR | INVESTOR | FACILITY_OWNER | CUSTOMER"),
        sa.Column(
            "evidence_id",
            sa.Integer(),
            sa.ForeignKey("global_event_evidence.id"),
            nullable=False,
            comment="연결 근거 필수 — 근거 없는 연결 방지",
        ),
        sa.Column("verification_status", sa.String(20), nullable=False,
                  server_default="proposed",
                  comment="proposed | verified | rejected (evidence와 독립 관리)"),
        sa.Column("confidence", sa.Float(), nullable=True,
                  comment="0.0~1.0. NULL = 미산정"),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_ecl_event_company", "event_company_link",
                    ["event_id", "company_id"])
    op.create_index("ix_ecl_event_role", "event_company_link",
                    ["event_id", "role"])
    op.create_index("uq_ecl_event_company_role", "event_company_link",
                    ["event_id", "company_id", "role"], unique=True)


def downgrade() -> None:
    # EventCompanyLink 먼저 삭제 (GlobalEventEvidence 참조)
    op.drop_index("uq_ecl_event_company_role", table_name="event_company_link")
    op.drop_index("ix_ecl_event_role", table_name="event_company_link")
    op.drop_index("ix_ecl_event_company", table_name="event_company_link")
    op.drop_table("event_company_link")

    op.drop_index("uq_gee_evidence_hash", table_name="global_event_evidence")
    op.drop_index("ix_gee_published_at", table_name="global_event_evidence")
    op.drop_index("ix_gee_event_status", table_name="global_event_evidence")
    op.drop_table("global_event_evidence")

    op.drop_index("ix_global_event_verification_status", table_name="global_event")
    op.drop_column("global_event", "event_schema_version")
    op.drop_column("global_event", "collected_at")
    op.drop_column("global_event", "verification_status")
