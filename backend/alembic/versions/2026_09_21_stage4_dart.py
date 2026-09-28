"""stage 4 DART filing and financial lineage"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260921_stage4"
down_revision: Union[str, None] = "20260921_stage3_complete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dart_sync_state",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("last_filing_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.PrimaryKeyConstraint("company_id"),
    )
    with op.batch_alter_table("filing") as batch:
        batch.add_column(sa.Column("report_name", sa.String(500), nullable=True))
        batch.add_column(sa.Column("available_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("raw_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("parser_version", sa.String(30), nullable=False, server_default="dart_v1"))
        batch.create_index("ix_filing_available_at", ["available_at"])
    op.execute("UPDATE filing SET available_at = filed_at WHERE available_at IS NULL")
    with op.batch_alter_table("filing") as batch:
        batch.alter_column("available_at", nullable=False)

    with op.batch_alter_table("financial_fact") as batch:
        batch.add_column(sa.Column("filing_id", sa.String(14), nullable=True))
        batch.add_column(sa.Column("business_year", sa.String(4), nullable=True))
        batch.add_column(sa.Column("report_code", sa.String(5), nullable=True))
        batch.add_column(sa.Column("fs_div", sa.String(3), nullable=True))
        batch.add_column(sa.Column("statement_div", sa.String(10), nullable=True))
        batch.create_index("ix_financial_fact_filing_id", ["filing_id"])
        batch.create_foreign_key("fk_financial_fact_filing", "filing", ["filing_id"], ["rcept_no"])
        batch.create_index("uq_financial_fact_identity", ["company_id", "filing_id", "fs_div", "account_id", "period"], unique=True)

    with op.batch_alter_table("disclosure_event") as batch:
        batch.add_column(sa.Column("financial_basis_filed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("raw_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disclosure_event") as batch:
        batch.drop_column("raw_data")
        batch.drop_column("financial_basis_filed_at")
    with op.batch_alter_table("financial_fact") as batch:
        batch.drop_index("uq_financial_fact_identity")
        batch.drop_constraint("fk_financial_fact_filing", type_="foreignkey")
        batch.drop_index("ix_financial_fact_filing_id")
        for name in ("statement_div", "fs_div", "report_code", "business_year", "filing_id"):
            batch.drop_column(name)
    with op.batch_alter_table("filing") as batch:
        batch.drop_index("ix_filing_available_at")
        for name in ("parser_version", "raw_hash", "available_at", "report_name"):
            batch.drop_column(name)
    op.drop_table("dart_sync_state")
