"""Join AI conversations to shared items and support project-targeted reviews."""
import json
import uuid
from alembic import op
import sqlalchemy as sa

revision = '20261003_context_links'
down_revision = '20261003_origins'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('reviews') as batch:
        batch.add_column(sa.Column('project_uuid',sa.String(36),nullable=True))
        batch.create_index('ix_reviews_project_uuid',['project_uuid'])
        batch.create_foreign_key('fk_reviews_project_uuid','projects',['project_uuid'],['uuid'],ondelete='SET NULL')
    with op.batch_alter_table('ai_conversations') as batch:
        batch.add_column(sa.Column('entry_uuid',sa.String(36),nullable=True))
        batch.create_index('ix_ai_conversations_entry_uuid',['entry_uuid'],unique=True)
        batch.create_foreign_key('fk_conversations_entry_uuid','entries',['entry_uuid'],['uuid'],ondelete='CASCADE')
    conn=op.get_bind()
    for item in conn.execute(sa.text('SELECT * FROM ai_conversations')).mappings():
        content='## 我的消息\n\n'+item['user_message']+'\n\n## AI 回复\n\n'+item['assistant_message']
        values=dict(uuid=item['uuid'],date=item['conversation_date'],title=item['conversation_title'],content=content,
            summary=item['summary'],category='AI 对话记录',sub_category='',importance=3,status='有效',source=item['source_file'],
            location_text='',notes='',privacy_level=item['privacy_level'],is_favorite=False,is_archived=False,deleted_at=None,
            content_hash='',created_at=item['created_at'],updated_at=item['updated_at'],metadata_json=json.dumps({'platform':item['platform']},ensure_ascii=False),
            item_type='conversations',origin=item['origin'],content_nature='ai_generated',original_content=content,normalized_content='',ai_summary='',
            generated_by_ai=True,ai_provider=item['platform'],ai_model='',generated_at=item['created_at'])
        names=list(values)
        conn.execute(sa.text('INSERT INTO entries ('+','.join(names)+') VALUES ('+','.join(':'+k for k in names)+')'),values)
        entry_id=conn.execute(sa.text('SELECT id FROM entries WHERE uuid=:uuid'),{'uuid':item['uuid']}).scalar()
        if item['project_id']:
            conn.execute(sa.text('INSERT INTO entry_projects(entry_id,project_id) VALUES(:entry,:project)'),{'entry':entry_id,'project':item['project_id']})
        for name in json.loads(item['tags'] or '[]'):
            if not isinstance(name,str) or not name.strip():
                continue
            tag_id=conn.execute(sa.text('SELECT id FROM tags WHERE name=:name'),{'name':name}).scalar()
            if not tag_id:
                conn.execute(sa.text('INSERT INTO tags(name,color,uuid,created_at,updated_at,metadata_json,origin) VALUES(:name,:color,:uuid,:created,:updated,:metadata,:origin)'),
                    dict(name=name,color='#409a84',uuid=str(uuid.uuid4()),created=item['created_at'],updated=item['updated_at'],metadata='{}',origin=item['origin']))
                tag_id=conn.execute(sa.text('SELECT id FROM tags WHERE name=:name'),{'name':name}).scalar()
            conn.execute(sa.text('INSERT INTO entry_tags(entry_id,tag_id) VALUES(:entry,:tag)'),{'entry':entry_id,'tag':tag_id})
        conn.execute(sa.text('UPDATE ai_conversations SET entry_uuid=uuid WHERE uuid=:uuid'),{'uuid':item['uuid']})

def downgrade():
    raise RuntimeError('For data safety, restore a backup with the matching app version instead of schema downgrade.')
