"""add raw_hash to daily_price

Revision ID: 20260921_stage3
Revises: 20260921_stage2
Create Date: 2026-09-21

3단계: daily_price 테이블에 raw_hash 컬럼 추가.
원본 응답의 SHA-256 해시를 저장하여 재현성을 보존한다.
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260921_stage3'
down_revision: Union[str, None] = '20260921_stage2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """daily_price 테이블에 raw_hash 컬럼 추가."""
    op.add_column(
        'daily_price',
        sa.Column(
            'raw_hash',
            sa.String(64),
            nullable=True,
            comment='원본 응답 SHA-256 해시 (64자 hex). 재현성 보존용.'
        )
    )
    # 빠른 조회를 위한 인덱스
    op.create_index(
        'idx_daily_price_raw_hash',
        'daily_price',
        ['raw_hash'],
        unique=False,
    )


def downgrade() -> None:
    """raw_hash 컬럼 제거."""
    op.drop_index('idx_daily_price_raw_hash', table_name='daily_price')
    op.drop_column('daily_price', 'raw_hash')
