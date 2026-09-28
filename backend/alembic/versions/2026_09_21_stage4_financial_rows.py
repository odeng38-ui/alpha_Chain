"""preserve repeated detailed financial statement rows"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260921_stage4_rows"
down_revision: Union[str, None] = "20260921_stage4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("financial_fact") as batch:
        batch.drop_index("uq_financial_fact_identity")
        batch.add_column(sa.Column("account_detail", sa.String(500), nullable=True))
        batch.add_column(sa.Column("row_key", sa.String(64), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("UPDATE financial_fact SET row_key = md5(id::text) WHERE row_key IS NULL")
    else:
        op.execute("UPDATE financial_fact SET row_key = printf('%064d', id) WHERE row_key IS NULL")
    with op.batch_alter_table("financial_fact") as batch:
        batch.alter_column("row_key", nullable=False)
        batch.create_index("uq_financial_fact_row", ["company_id", "filing_id", "fs_div", "row_key"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("financial_fact") as batch:
        batch.drop_index("uq_financial_fact_row")
        batch.drop_column("row_key")
        batch.drop_column("account_detail")
        batch.create_index("uq_financial_fact_identity", ["company_id", "filing_id", "fs_div", "account_id", "period"], unique=True)
