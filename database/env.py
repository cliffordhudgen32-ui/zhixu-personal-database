from alembic import context
from app.database import engine, Base
from app import models
from app import knowledge_models
from app import extraction_models, semantic_models, workflow_models, capture_models

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True,
                      include_object=lambda obj, name, type_, reflected, compare_to: not name.startswith('entries_fts') if name else True)
    with context.begin_transaction():
        context.run_migrations()
