"""add advertising tables

Revision ID: b7e3f8a91c42
Revises: a1b2c3d4e5f6
Create Date: 2026-03-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e3f8a91c42'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ad_campaigns',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('platform', sa.String(length=50), nullable=False),
        sa.Column('campaign_type', sa.String(length=50), nullable=False, server_default='awareness'),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='draft'),
        sa.Column('budget_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('spent_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('target_audience', sa.JSON(), nullable=True),
        sa.Column('schedule', sa.JSON(), nullable=True),
        sa.Column('performance', sa.JSON(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('launched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_ad_campaigns_business_id', 'ad_campaigns', ['business_id'])

    op.create_table(
        'ad_copies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.Integer(), sa.ForeignKey('ad_campaigns.id'), nullable=True),
        sa.Column('headline', sa.String(length=500), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('cta', sa.String(length=100), nullable=True),
        sa.Column('image_prompt', sa.Text(), nullable=True),
        sa.Column('platform_format', sa.String(length=50), nullable=False),
        sa.Column('tone', sa.String(length=50), nullable=False, server_default='professional'),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='draft'),
        sa.Column('performance', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_ad_copies_campaign_id', 'ad_copies', ['campaign_id'])

    op.create_table(
        'customer_segments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('demographics', sa.JSON(), nullable=True),
        sa.Column('psychographics', sa.JSON(), nullable=True),
        sa.Column('behaviors', sa.JSON(), nullable=True),
        sa.Column('estimated_size', sa.String(length=100), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('platform_targeting', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_customer_segments_business_id', 'customer_segments', ['business_id'])


def downgrade() -> None:
    op.drop_table('customer_segments')
    op.drop_table('ad_copies')
    op.drop_table('ad_campaigns')
