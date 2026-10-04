"""An empty local database reports a known zero to the desktop control panel."""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ai_routes
from app.database import Base, LOCK


def test_empty_ai_draft_api_explicitly_reports_zero_pending_review(tmp_path, monkeypatch, client):
    engine = create_engine('sqlite:///' + (tmp_path / 'empty-ai-drafts.db').as_posix())
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def isolated_transaction():
        with LOCK, factory.begin() as session:
            yield session

    monkeypatch.setattr(ai_routes, 'transaction', isolated_transaction)
    try:
        response = client.get('/api/ai/drafts?size=1')
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['items'] == [] and result['total'] == 0
        assert result['counts']['pending_review'] == 0
        assert isinstance(result['counts']['pending_review'], int)
    finally:
        engine.dispose()
