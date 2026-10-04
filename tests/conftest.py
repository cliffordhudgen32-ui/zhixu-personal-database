import os
import tempfile
from pathlib import Path
import pytest

TEST_HOME = tempfile.TemporaryDirectory(prefix='pld-tests-')
os.environ['PLD_HOME'] = TEST_HOME.name
os.environ['DATABASE_URL'] = 'sqlite:///' + (Path(TEST_HOME.name)/'data/personal.db').as_posix()
os.environ['PLD_WORKERS_DISABLED'] = '1'

from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture(scope='session', autouse=True)
def cleanup_test_home():
    yield
    from app.main import handler
    from app.database import engine
    engine.dispose()
    handler.close()
    TEST_HOME.cleanup()

@pytest.fixture(scope='session')
def client():
    with TestClient(app) as client:
        client.headers['x-local-token']=client.get('/api/session').json()['token']
        yield client

@pytest.fixture
def create(client):
    def make(kind='entries',**data):
        result=client.post('/api/'+kind,json=data)
        assert result.status_code==200,result.text
        return result.json()
    return make
