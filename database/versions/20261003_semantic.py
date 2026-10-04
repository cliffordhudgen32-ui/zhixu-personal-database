"""Add rebuildable local semantic vectors with precise source evidence."""
from alembic import op
import sqlalchemy as sa

revision = '20261003_semantic'
down_revision = '20261003_extract'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('embedding_chunks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entry_uuid', sa.String(36), sa.ForeignKey('entries.uuid', ondelete='CASCADE'), nullable=False),
        sa.Column('model', sa.String(200), nullable=False),
        sa.Column('model_version', sa.String(200), nullable=False),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('entry_updated_at', sa.String(40), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('field', sa.String(100), nullable=False),
        sa.Column('attachment_uuid', sa.String(36), sa.ForeignKey('attachments.uuid', ondelete='CASCADE'), nullable=True),
        sa.Column('attachment_sha256', sa.String(64), nullable=True),
        sa.Column('page', sa.Integer(), nullable=True),
        sa.Column('source_label', sa.String(500), nullable=False),
        sa.Column('start_offset', sa.Integer(), nullable=False),
        sa.Column('end_offset', sa.Integer(), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=False),
        sa.Column('dimension', sa.Integer(), nullable=False),
        sa.Column('vector', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.String(40), nullable=False),
        sa.UniqueConstraint('entry_uuid', 'model_version', 'chunk_index', name='uq_embedding_chunk_position'))
    for column in ('entry_uuid', 'model_version', 'source_hash', 'attachment_uuid'):
        op.create_index('ix_embedding_chunks_' + column, 'embedding_chunks', [column])
    op.create_index('ix_embedding_chunk_model_entry', 'embedding_chunks', ['model_version', 'entry_uuid'])


def downgrade():
    op.drop_table('embedding_chunks')
