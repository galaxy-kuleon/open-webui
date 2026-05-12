"""add feedback outbox

Revision ID: b0c1d2e3f4a5
Revises: a0b1c2d3e4f5
Create Date: 2026-05-10
"""

from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b0c1d2e3f4a5'
down_revision: Union[str, None] = 'a0b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'feedback_outbox',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('event_id', sa.Text(), nullable=False),
        sa.Column('feedback_id', sa.Text(), nullable=False),
        sa.Column('feedback_version', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('lease_until', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_feedback_outbox_event_id', 'feedback_outbox', ['event_id'], unique=True)
    op.create_index('ix_feedback_outbox_feedback_id', 'feedback_outbox', ['feedback_id'])
    op.create_index(
        'ix_feedback_outbox_feedback_version',
        'feedback_outbox',
        ['feedback_id', 'feedback_version'],
    )
    op.create_index('ix_feedback_outbox_user_id', 'feedback_outbox', ['user_id'])
    op.create_index('ix_feedback_outbox_status', 'feedback_outbox', ['status'])


def downgrade():
    op.drop_index('ix_feedback_outbox_status', table_name='feedback_outbox')
    op.drop_index('ix_feedback_outbox_user_id', table_name='feedback_outbox')
    op.drop_index('ix_feedback_outbox_feedback_version', table_name='feedback_outbox')
    op.drop_index('ix_feedback_outbox_feedback_id', table_name='feedback_outbox')
    op.drop_index('ix_feedback_outbox_event_id', table_name='feedback_outbox')
    op.drop_table('feedback_outbox')
