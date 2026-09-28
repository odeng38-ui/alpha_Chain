"""expand XBRL account identifiers"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260921_stage4_account"
down_revision: Union[str, None] = "20260921_stage4_rows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("financial_fact") as batch:
        batch.alter_column("account_id", existing_type=sa.String(100), type_=sa.String(500), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("financial_fact") as batch:
        batch.alter_column("account_id", existing_type=sa.String(500), type_=sa.String(100), nullable=False)
