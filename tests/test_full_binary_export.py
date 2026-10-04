"""Exercise the full disaster-recovery JSON artifact with actual SQLite BLOB data."""
import base64
from datetime import date
import json
from pathlib import Path
import struct
import tempfile

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AuditLog, DailyEntry
from app.semantic_models import EmbeddingChunk
from app.services import exporting


def test_full_json_preserves_vector_bytes_sensitive_records_and_recycle_bin(tmp_path, monkeypatch):
    # Both the source database and the produced artifact are removed after this
    # test; neither the shared client database nor the user's D: database is used.
    with tempfile.TemporaryDirectory(prefix='full-export-', dir=tmp_path) as temporary:
        isolated_home = Path(temporary)
        monkeypatch.setattr(exporting, 'HOME', isolated_home)
        engine = create_engine('sqlite:///' + (isolated_home / 'source.db').as_posix())

        @event.listens_for(engine, 'connect')
        def enforce_foreign_keys(connection, record):
            connection.execute('PRAGMA foreign_keys=ON')

        factory = sessionmaker(engine, expire_on_commit=False)
        Base.metadata.create_all(engine)
        vector_bytes = struct.pack('<4f', 0.6, -0.8, 0.0, -0.0)
        try:
            with factory.begin() as session:
                public = DailyEntry(title='整库导出公开记录', content='公开正文', date=date(2026, 10, 3))
                sensitive = DailyEntry(title='整库导出敏感记录', content='仅供本人灾备的私密正文',
                                       original_content='不可丢失的原始文字', privacy_level=3,
                                       date=date(2026, 10, 3), metadata_json={'保留范围': '完整灾备'})
                recycled = DailyEntry(title='回收站仍须灾备', content='回收站正文', date=date(2026, 10, 2),
                                      deleted_at='2026-10-03T00:00:00+00:00')
                session.add_all([public, sensitive, recycled])
                session.flush()
                session.add(EmbeddingChunk(entry_uuid=sensitive.uuid, model='export-test-model',
                    model_version='export-test-weights-v1', source_hash='a' * 64,
                    entry_updated_at=sensitive.updated_at, chunk_index=0, field='content',
                    source_label=sensitive.title, start_offset=0, end_offset=len(sensitive.content),
                    evidence=sensitive.content, dimension=4, vector=vector_bytes))
                session.add(AuditLog(action='测试灾备留痕', entity='entries', object_uuid=sensitive.uuid,
                                     description='整库导出应保留既有审计日志'))
                identities = {entry.uuid for entry in (public, sensitive, recycled)}

            with factory.begin() as session:
                artifact = exporting.export_database(session, 'json')
                assert artifact.parent == isolated_home / 'exports'
                assert artifact.is_file()
                exported = json.loads(artifact.read_text(encoding='utf-8'))

            assert exported['format'] == 'pld-full' and exported['version'] == 1
            tables = exported['tables']
            assert set(tables) == set(Base.metadata.tables)
            records = {row['uuid']: row for row in tables['entries']}
            assert set(records) == identities
            assert records[sensitive.uuid]['privacy_level'] == 3
            assert records[sensitive.uuid]['content'] == sensitive.content
            assert records[sensitive.uuid]['original_content'] == sensitive.original_content
            assert records[sensitive.uuid]['metadata_json'] == {'保留范围': '完整灾备'}
            assert records[sensitive.uuid]['date'] == '2026-10-03'
            assert records[recycled.uuid]['deleted_at'] == recycled.deleted_at
            assert any(row['action'] == '测试灾备留痕' and row['object_uuid'] == sensitive.uuid
                       for row in tables['audit_logs'])

            assert len(tables['embedding_chunks']) == 1
            chunk = tables['embedding_chunks'][0]
            assert set(chunk) == set(EmbeddingChunk.__table__.columns.keys())
            assert chunk['entry_uuid'] == sensitive.uuid
            assert chunk['dimension'] == 4 and chunk['model_version'] == 'export-test-weights-v1'
            assert chunk['evidence'] == sensitive.content
            assert chunk['vector']['$binary'] == 'base64'
            restored_bytes = base64.b64decode(chunk['vector']['data'], validate=True)
            assert restored_bytes == vector_bytes

            # Restore the exported row through SQLAlchemy's actual LargeBinary
            # column to prove the artifact can recover the SQLite BLOB, including
            # zero bytes and a negative-zero sign bit, without string coercion.
            with factory.begin() as session:
                session.query(EmbeddingChunk).delete()
                recovered = dict(chunk, vector=restored_bytes)
                session.execute(EmbeddingChunk.__table__.insert().values(**recovered))
            with factory() as session:
                restored = session.scalar(select(EmbeddingChunk))
                assert restored.vector == vector_bytes
                assert struct.unpack('<4f', restored.vector) == struct.unpack('<4f', vector_bytes)
        finally:
            engine.dispose()
