"""add roadmap tables

Revision ID: a1b2c3d4e5f6
Revises: 39e4e92771db
Create Date: 2026-03-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '39e4e92771db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # New columns on businesses
    op.add_column('businesses', sa.Column('roadmap_day', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('businesses', sa.Column('current_phase', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('businesses', sa.Column('graduation_date', sa.DateTime(timezone=True), nullable=True))
    op.add_column('businesses', sa.Column('health_score', sa.Float(), nullable=True))

    # New columns on content
    op.add_column('content', sa.Column('milestone_key', sa.String(length=100), nullable=True))
    op.add_column('content', sa.Column('distribution_status', sa.String(length=50), nullable=True))
    op.add_column('content', sa.Column('distribution_results', sa.JSON(), nullable=True))

    # New tables
    op.create_table(
        'roadmap_phases',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('phase_number', sa.Integer(), nullable=False),
        sa.Column('phase_name', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='locked'),
        sa.Column('graduation_score', sa.Float(), nullable=True),
        sa.Column('graduation_criteria', sa.JSON(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_roadmap_phases_business_id', 'roadmap_phases', ['business_id'])

    op.create_table(
        'milestones',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('phase_number', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='pending'),
        sa.Column('target_value', sa.Float(), nullable=True),
        sa.Column('current_value', sa.Float(), nullable=True),
        sa.Column('evidence_json', sa.JSON(), nullable=True),
        sa.Column('due_day', sa.Integer(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_milestones_business_id', 'milestones', ['business_id'])
    op.create_index('ix_milestones_key', 'milestones', ['key'])

    op.create_table(
        'business_health_scores',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('score_type', sa.String(length=50), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('factors', sa.JSON(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_business_health_scores_business_id', 'business_health_scores', ['business_id'])

    op.create_table(
        'revenue_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('source', sa.String(length=100), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='usd'),
        sa.Column('attribution_json', sa.JSON(), nullable=True),
        sa.Column('event_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_revenue_events_business_id', 'revenue_events', ['business_id'])

    op.create_table(
        'advisory_notes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('acknowledged', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_advisory_notes_business_id', 'advisory_notes', ['business_id'])

    op.create_table(
        'distribution_channels',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('platform', sa.String(length=100), nullable=False),
        sa.Column('config_json', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='active'),
        sa.Column('last_published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_distribution_channels_business_id', 'distribution_channels', ['business_id'])

    op.create_table(
        'competitive_monitors',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('competitor_name', sa.String(length=255), nullable=False),
        sa.Column('competitor_url', sa.String(length=500), nullable=True),
        sa.Column('findings_json', sa.JSON(), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_competitive_monitors_business_id', 'competitive_monitors', ['business_id'])


def downgrade() -> None:
    """Downgrade schema."""
    # Drop new tables
    op.drop_index('ix_competitive_monitors_business_id', table_name='competitive_monitors')
    op.drop_table('competitive_monitors')
    op.drop_index('ix_distribution_channels_business_id', table_name='distribution_channels')
    op.drop_table('distribution_channels')
    op.drop_index('ix_advisory_notes_business_id', table_name='advisory_notes')
    op.drop_table('advisory_notes')
    op.drop_index('ix_revenue_events_business_id', table_name='revenue_events')
    op.drop_table('revenue_events')
    op.drop_index('ix_business_health_scores_business_id', table_name='business_health_scores')
    op.drop_table('business_health_scores')
    op.drop_index('ix_milestones_key', table_name='milestones')
    op.drop_index('ix_milestones_business_id', table_name='milestones')
    op.drop_table('milestones')
    op.drop_index('ix_roadmap_phases_business_id', table_name='roadmap_phases')
    op.drop_table('roadmap_phases')

    # Drop new content columns
    op.drop_column('content', 'distribution_results')
    op.drop_column('content', 'distribution_status')
    op.drop_column('content', 'milestone_key')

    # Drop new business columns
    op.drop_column('businesses', 'health_score')
    op.drop_column('businesses', 'graduation_date')
    op.drop_column('businesses', 'current_phase')
    op.drop_column('businesses', 'roadmap_day')
