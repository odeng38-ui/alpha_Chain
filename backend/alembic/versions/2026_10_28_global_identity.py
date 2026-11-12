
"""add global company identity foundation"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_global_identity"
down_revision: Union[str, None] = "20261005_news_validation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EXCLUSIVE_SOURCES = (
    "'DART_CORP_CODE', 'CIK', 'LEI', "
    "'US_TICKER', 'KRX_TICKER', 'ISIN'"
)
_INDEX_NAME = "uq_identifier_active_exclusive"


def upgrade() -> None:
    op.add_column("company", sa.Column("country_code", sa.String(2), nullable=True))
    op.add_column("company", sa.Column("legal_name", sa.String(300), nullable=True))
    op.add_column("company", sa.Column("normalized_name", sa.String(300), nullable=True))
    op.add_column("company", sa.Column("entity_type", sa.String(30), nullable=True))
    op.add_column("company", sa.Column("verification_status", sa.String(20), nullable=True))

    op.create_index("ix_company_country_code", "company", ["country_code"])
    op.create_index("ix_company_normalized_name", "company", ["normalized_name"])
    op.create_index(
        "ix_company_verification_status",
        "company",
        ["verification_status"],
    )
    if op.get_context().as_sql:
        op.create_index(
            _INDEX_NAME,
            "identifier_map",
            ["source", "source_id_value"],
            unique=True,
            postgresql_where=sa.text(
                f"effective_to IS NULL AND source IN ({_EXCLUSIVE_SOURCES})"
            ),
        )
        return
    bind = op.get_bind()

    existing = bind.execute(
        sa.text("""
            SELECT
                i.indisunique,
                i.indisvalid,
                i.indisready,
                i.indrelid = 'public.identifier_map'::regclass
                    AS correct_table,
                pg_get_indexdef(c.oid) AS definition
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_index i ON i.indexrelid = c.oid
            WHERE n.nspname = 'public'
              AND c.relname = :index_name
        """),
        {"index_name": _INDEX_NAME},
    ).mappings().first()

    if existing is None:
        # Detect a same-name relation that is not an index.
        relation_exists = bind.execute(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"public.{_INDEX_NAME}"},
        ).scalar()

        if relation_exists:
            raise RuntimeError(
                f"{_INDEX_NAME} exists but is not a usable index"
            )

        op.create_index(
            _INDEX_NAME,
            "identifier_map",
            ["source", "source_id_value"],
            unique=True,
            postgresql_where=sa.text(
                f"effective_to IS NULL AND source IN ({_EXCLUSIVE_SOURCES})"
            ),
        )
    else:
        definition = existing["definition"] or ""
        required_fragments = (
            "(source, source_id_value)",
            "effective_to IS NULL",
            "DART_CORP_CODE",
            "CIK",
            "LEI",
            "US_TICKER",
            "KRX_TICKER",
            "ISIN",
        )

        if (
            not existing["indisunique"]
            or not existing["indisvalid"]
            or not existing["indisready"]
            or not existing["correct_table"]
            or not all(fragment in definition for fragment in required_fragments)
        ):
            raise RuntimeError(
                f"Existing {_INDEX_NAME} definition requires manual review"
            )


def downgrade() -> None:
    # Preserve the identifier index because it may predate this migration.
    # This also intentionally retains an index created by upgrade().
    op.drop_index("ix_company_verification_status", table_name="company")
    op.drop_index("ix_company_normalized_name", table_name="company")
    op.drop_index("ix_company_country_code", table_name="company")

    op.drop_column("company", "verification_status")
    op.drop_column("company", "entity_type")
    op.drop_column("company", "normalized_name")
    op.drop_column("company", "legal_name")
    op.drop_column("company", "country_code")
