"""Add event_log table for domain events

Revision ID: 002
Revises: 001
Create Date: 2025-12-22

Таблица для хранения доменных событий с корреляцией для тестирования.
Источник: docs/ru/10-testing-framework.md
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === EVENT_LOG (Доменные события) ===
    op.create_table(
        'event_log',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('event_id', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('event_type', sa.String(64), nullable=False),
        sa.Column('event_data', postgresql.JSONB, nullable=False, default={}),
        
        # Correlation fields (for testing and tracing)
        sa.Column('run_id', postgresql.UUID(as_uuid=True)),
        sa.Column('scenario_id', sa.String(32)),
        sa.Column('request_id', postgresql.UUID(as_uuid=True)),
        sa.Column('tx_id', postgresql.UUID(as_uuid=True)),
        sa.Column('actor_pid', sa.String(128)),
        
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
    )
    
    # Indexes for common queries
    op.create_index('idx_event_log_event_type', 'event_log', ['event_type'])
    op.create_index('idx_event_log_actor_pid', 'event_log', ['actor_pid'])
    op.create_index('idx_event_log_tx_id', 'event_log', ['tx_id'])
    op.create_index('idx_event_log_request_id', 'event_log', ['request_id'])
    op.create_index('idx_event_log_run_scenario', 'event_log', ['run_id', 'scenario_id'])
    op.create_index('idx_event_log_created_at', 'event_log', ['created_at'])


def downgrade() -> None:
    op.drop_table('event_log')
