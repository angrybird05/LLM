"""Add columns for agentic loop history round-tripping

- messages.seq: per-session insertion order (timestamps have 1s resolution on SQLite)
- tool_calls.tool_use_id: provider-side tool_use id needed to rebuild valid tool history

Revision ID: 002
Revises: 001
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('seq', sa.Integer(), nullable=True))
    op.add_column('tool_calls', sa.Column('tool_use_id', sa.String(200), nullable=True))
    # Backfill ordering for pre-existing rows by (session, created_at)
    op.execute("""
        UPDATE messages SET seq = (
            SELECT COUNT(*) FROM messages AS m2
            WHERE m2.session_id = messages.session_id
              AND (m2.created_at < messages.created_at
                   OR (m2.created_at = messages.created_at AND m2.id <= messages.id))
        )
    """)


def downgrade() -> None:
    op.drop_column('tool_calls', 'tool_use_id')
    op.drop_column('messages', 'seq')
