"""Add AI review workflow, configuration and durable local jobs."""
from alembic import op
from app.database import Base
from app import workflow_models

revision = '20261003_workflow'
down_revision = '20261003_semantic'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('ai_configuration', 'ai_drafts', 'ai_archives', 'ai_review_revisions', 'ai_jobs'):
        Base.metadata.tables[name].create(op.get_bind(), checkfirst=True)


def downgrade():
    for name in ('ai_jobs', 'ai_review_revisions', 'ai_archives', 'ai_drafts', 'ai_configuration'):
        Base.metadata.tables[name].drop(op.get_bind(), checkfirst=True)
