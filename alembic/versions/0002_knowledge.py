"""knowledge layer: identity and taxonomy decisions, AI cost fields on ai_traces

Revision ID: 0002_knowledge
Revises: 0001_baseline
Create Date: 2026-09-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0002_knowledge'
down_revision: Union[str, None] = '0001_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('identity_decisions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('market_name', sa.String(length=255), nullable=False),
    sa.Column('listing_a', sa.String(length=64), nullable=False),
    sa.Column('listing_b', sa.String(length=64), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('decided_by', sa.String(length=255), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_identity_decisions_market_name'), 'identity_decisions', ['market_name'], unique=False)
    op.create_table('taxonomy_decisions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('market_name', sa.String(length=255), nullable=False),
    sa.Column('node_key', sa.String(length=512), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('decided_by', sa.String(length=255), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_taxonomy_decisions_market_name'), 'taxonomy_decisions', ['market_name'], unique=False)
    op.add_column('ai_traces', sa.Column('input_tokens', sa.Integer(), nullable=True))
    op.add_column('ai_traces', sa.Column('output_tokens', sa.Integer(), nullable=True))
    op.add_column('ai_traces', sa.Column('cost_usd', sa.Float(), nullable=True))
    op.add_column('ai_traces', sa.Column('market_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_traces', 'market_name')
    op.drop_column('ai_traces', 'cost_usd')
    op.drop_column('ai_traces', 'output_tokens')
    op.drop_column('ai_traces', 'input_tokens')
    op.drop_index(op.f('ix_taxonomy_decisions_market_name'), table_name='taxonomy_decisions')
    op.drop_table('taxonomy_decisions')
    op.drop_index(op.f('ix_identity_decisions_market_name'), table_name='identity_decisions')
    op.drop_table('identity_decisions')
