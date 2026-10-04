"""Keep capture text separate until the user explicitly submits it."""
from alembic import op

from app.database import Base
from app import capture_models

revision = '20261004_capture'
down_revision = '20261003_workflow'
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.tables['capture_drafts'].create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('For data safety, restore a matching-version backup instead of discarding capture drafts.')
