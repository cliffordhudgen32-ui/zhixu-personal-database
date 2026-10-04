"""Track origin consistently for all non-envelope content and reference entities."""
from alembic import op
import sqlalchemy as sa

revision = '20261003_origins'
down_revision = '03ae2f1407ba'
branch_labels = None
depends_on = None
TABLES = ('tags','projects','people','tasks','events','attachments','links',
          'custom_fields','ai_conversations','reviews','domains','topics',
          'knowledge_relations','revisions','reuse_logs')

def upgrade():
    for table in TABLES:
        op.add_column(table,sa.Column('origin',sa.String(30),nullable=False,server_default='manual'))
        op.create_index('ix_'+table+'_origin',table,['origin'])

def downgrade():
    raise RuntimeError('For data safety, restore a backup with the matching app version instead of schema downgrade.')
