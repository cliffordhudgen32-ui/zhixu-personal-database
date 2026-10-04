"""Persist local attachment text separately from immutable original files."""
from alembic import op
import sqlalchemy as sa

revision = '20261003_extract'
down_revision = '20261003_context_links'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('attachment_extractions',
        sa.Column('attachment_uuid', sa.String(36), nullable=False),
        sa.Column('source_sha256', sa.String(64), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('pages', sa.JSON(), nullable=False),
        sa.Column('tool', sa.String(100), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('truncated', sa.Boolean(), nullable=False),
        sa.Column('parser_version', sa.String(30), nullable=False),
        sa.Column('job_token', sa.String(36), nullable=False),
        sa.Column('created_at', sa.String(40), nullable=False),
        sa.Column('updated_at', sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(['attachment_uuid'], ['attachments.uuid'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('attachment_uuid'))
    op.create_index('ix_attachment_extractions_source_sha256', 'attachment_extractions', ['source_sha256'])
    op.create_index('ix_attachment_extractions_status', 'attachment_extractions', ['status'])


def downgrade():
    raise RuntimeError('Restore a matching-version backup instead of discarding derived text.')
