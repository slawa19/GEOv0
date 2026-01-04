"""Initial GEO Hub schema

Revision ID: 001
Revises: 
Create Date: 2025-12-13

Полная начальная схема БД для GEO Hub MVP v0.1
Источник: docs/ru/03-architecture.md, docs/ru/02-protocol-spec.md
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # UUID generation helper used by gen_random_uuid()
    op.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')
    # === EQUIVALENTS (Эквиваленты) ===
    op.create_table(
        'equivalents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, 
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('code', sa.String(16), nullable=False, unique=True),
        sa.Column('description', sa.Text),
        sa.Column('precision', sa.SmallInteger, nullable=False, default=2),
        sa.Column('metadata', postgresql.JSONB, default={}),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), 
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), 
                  server_default=sa.text('NOW()')),
    )
    op.create_index('idx_equivalents_code', 'equivalents', ['code'])

    # === PARTICIPANTS (Участники) ===
    op.create_table(
        'participants',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('pid', sa.String(64), nullable=False, unique=True),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('public_key', sa.String(64), nullable=False),
        sa.Column('type', sa.String(20), nullable=False, default='person'),
        sa.Column('status', sa.String(20), nullable=False, default='active'),
        sa.Column('verification_level', sa.SmallInteger, nullable=False, default=0),
        sa.Column('profile', postgresql.JSONB, default={}),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
    )
    op.create_index('idx_participants_pid', 'participants', ['pid'])
    op.create_index('idx_participants_status', 'participants', ['status'])
    op.create_index('idx_participants_display_name', 'participants', ['display_name'])
    
    # Check constraint для type
    op.execute("""
        ALTER TABLE participants 
        ADD CONSTRAINT chk_participant_type 
        CHECK (type IN ('person', 'organization', 'hub'))
    """)
    
    # Check constraint для status
    op.execute("""
        ALTER TABLE participants 
        ADD CONSTRAINT chk_participant_status 
        CHECK (status IN ('active', 'suspended', 'left', 'deleted'))
    """)

    # === TRUST_LINES (Линии доверия) ===
    op.create_table(
        'trust_lines',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('from_participant_id', postgresql.UUID(as_uuid=True), 
                  sa.ForeignKey('participants.id'), nullable=False),
        sa.Column('to_participant_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('participants.id'), nullable=False),
        sa.Column('equivalent_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('equivalents.id'), nullable=False),
        sa.Column('limit', sa.Numeric(20, 8), nullable=False),
        sa.Column('policy', postgresql.JSONB, default={
            'auto_clearing': True,
            'can_be_intermediate': True,
            'daily_limit': None,
            'blocked_participants': []
        }),
        sa.Column('status', sa.String(20), nullable=False, default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
    )
    
    # Уникальность: одна линия между парой участников в одном эквиваленте
    op.create_unique_constraint(
        'uq_trust_lines_from_to_equivalent',
        'trust_lines',
        ['from_participant_id', 'to_participant_id', 'equivalent_id']
    )
    
    op.create_index('idx_trust_lines_from', 'trust_lines', ['from_participant_id'])
    op.create_index('idx_trust_lines_to', 'trust_lines', ['to_participant_id'])
    op.create_index('idx_trust_lines_equivalent', 'trust_lines', ['equivalent_id'])
    op.create_index('idx_trust_lines_status', 'trust_lines', ['status'])
    
    # Check constraint для status
    op.execute("""
        ALTER TABLE trust_lines 
        ADD CONSTRAINT chk_trust_line_status 
        CHECK (status IN ('active', 'frozen', 'closed'))
    """)
    
    # Check constraint для limit
    op.execute("""
        ALTER TABLE trust_lines 
        ADD CONSTRAINT chk_trust_line_limit_positive 
        CHECK (\"limit\" >= 0)
    """)

    # === DEBTS (Долги) ===
    op.create_table(
        'debts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('debtor_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('participants.id'), nullable=False),
        sa.Column('creditor_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('participants.id'), nullable=False),
        sa.Column('equivalent_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('equivalents.id'), nullable=False),
        sa.Column('amount', sa.Numeric(20, 8), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
    )
    
    # Уникальность: одна запись долга между парой в эквиваленте
    op.create_unique_constraint(
        'uq_debts_debtor_creditor_equivalent',
        'debts',
        ['debtor_id', 'creditor_id', 'equivalent_id']
    )
    
    op.create_index('idx_debts_debtor', 'debts', ['debtor_id'])
    op.create_index('idx_debts_creditor', 'debts', ['creditor_id'])
    op.create_index('idx_debts_equivalent', 'debts', ['equivalent_id'])
    
    # Check constraint для amount >= 0 (0 is used as a baseline row)
    op.execute("""
        ALTER TABLE debts 
        ADD CONSTRAINT chk_debt_amount_positive 
        CHECK (amount >= 0)
    """)

    # === TRANSACTIONS (Транзакции) ===
    op.create_table(
        'transactions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('tx_id', sa.String(64), nullable=False, unique=True),
        sa.Column('type', sa.String(30), nullable=False),
        sa.Column('initiator_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('participants.id'), nullable=False),
        sa.Column('payload', postgresql.JSONB, nullable=False),
        sa.Column('signatures', postgresql.JSONB, default=[]),
        sa.Column('state', sa.String(30), nullable=False, default='NEW'),
        sa.Column('error', postgresql.JSONB),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
    )
    
    op.create_index('idx_transactions_tx_id', 'transactions', ['tx_id'])
    op.create_index('idx_transactions_initiator', 'transactions', ['initiator_id'])
    op.create_index('idx_transactions_type', 'transactions', ['type'])
    op.create_index('idx_transactions_state', 'transactions', ['state'])
    op.create_index('idx_transactions_created_at', 'transactions', ['created_at'])
    
    # Check constraint для type
    op.execute("""
        ALTER TABLE transactions 
        ADD CONSTRAINT chk_transaction_type 
        CHECK (type IN (
            'TRUST_LINE_CREATE', 'TRUST_LINE_UPDATE', 'TRUST_LINE_CLOSE',
            'PAYMENT', 'CLEARING', 'COMPENSATION', 'COMMODITY_REDEMPTION'
        ))
    """)
    
    # Check constraint для state
    op.execute("""
        ALTER TABLE transactions 
        ADD CONSTRAINT chk_transaction_state 
        CHECK (state IN (
            'NEW', 'ROUTED', 'PREPARE_IN_PROGRESS', 'PREPARED',
            'COMMITTED', 'ABORTED', 'PROPOSED', 'WAITING', 'REJECTED'
        ))
    """)

    # === PREPARE_LOCKS (Блокировки для 2PC) ===
    op.create_table(
        'prepare_locks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('tx_id', sa.String(64), nullable=False),
        sa.Column('participant_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('participants.id'), nullable=False),
        sa.Column('effects', postgresql.JSONB, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
    )
    
    op.create_unique_constraint(
        'uq_prepare_locks_tx_participant',
        'prepare_locks',
        ['tx_id', 'participant_id']
    )
    
    op.create_index('idx_prepare_locks_tx_id', 'prepare_locks', ['tx_id'])
    op.create_index('idx_prepare_locks_expires_at', 'prepare_locks', ['expires_at'])

    # === AUTH_CHALLENGES (Challenge для аутентификации) ===
    op.create_table(
        'auth_challenges',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('pid', sa.String(64), nullable=False),
        sa.Column('challenge', sa.String(64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used', sa.Boolean, nullable=False, default=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
    )
    
    op.create_index('idx_auth_challenges_pid', 'auth_challenges', ['pid'])
    op.create_index('idx_auth_challenges_challenge', 'auth_challenges', ['challenge'])
    op.create_index('idx_auth_challenges_expires_at', 'auth_challenges', ['expires_at'])

    # === AUDIT_LOG (Журнал аудита) ===
    op.create_table(
        'audit_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('timestamp', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
        sa.Column('actor_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('participants.id')),
        sa.Column('actor_role', sa.String(50)),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('object_type', sa.String(50)),
        sa.Column('object_id', sa.String(64)),
        sa.Column('reason', sa.Text),
        sa.Column('before_state', postgresql.JSONB),
        sa.Column('after_state', postgresql.JSONB),
        sa.Column('request_id', sa.String(64)),
        sa.Column('ip_address', sa.String(45)),
        sa.Column('user_agent', sa.Text),
    )
    
    op.create_index('idx_audit_log_timestamp', 'audit_log', ['timestamp'])
    op.create_index('idx_audit_log_actor', 'audit_log', ['actor_id'])
    op.create_index('idx_audit_log_action', 'audit_log', ['action'])
    op.create_index('idx_audit_log_object', 'audit_log', ['object_type', 'object_id'])

    # === INTEGRITY_CHECKPOINTS (Контрольные точки целостности) ===
    op.create_table(
        'integrity_checkpoints',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('equivalent_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('equivalents.id'), nullable=False),
        sa.Column('checksum', sa.String(64), nullable=False),
        sa.Column('invariants_status', postgresql.JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
    )
    
    op.create_index('idx_integrity_checkpoints_equivalent', 
                    'integrity_checkpoints', ['equivalent_id'])
    op.create_index('idx_integrity_checkpoints_created_at', 
                    'integrity_checkpoints', ['created_at'])

    # === CONFIG (Конфигурация runtime) ===
    op.create_table(
        'config',
        sa.Column('key', sa.String(100), primary_key=True),
        sa.Column('value', postgresql.JSONB, nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
        sa.Column('updated_by', sa.String(64)),
    )


def downgrade() -> None:
    op.drop_table('config')
    op.drop_table('integrity_checkpoints')
    op.drop_table('audit_log')
    op.drop_table('auth_challenges')
    op.drop_table('prepare_locks')
    op.drop_table('transactions')
    op.drop_table('debts')
    op.drop_table('trust_lines')
    op.drop_table('participants')
    op.drop_table('equivalents')
