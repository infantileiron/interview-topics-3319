import os, sys, tempfile, pathlib
import pytest
# 每个测试进程使用独立 SQLite 文件，避免与演示库/其他用例串数据
_tmp = tempfile.mkdtemp(prefix='revision-test-')
os.environ['DATABASE_URL'] = f"sqlite:///{_tmp}/test.sqlite"
os.environ['SEED_DEMO'] = 'false'
os.environ['TASK_MODE'] = 'inline'
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from src.main import app
from src.utils import repository as repo
from src.utils.security import hash_password

@pytest.fixture(scope='session')
def client():
    repo.init_db()
    return TestClient(app)

@pytest.fixture()
def analyst_headers(client):
    repo.put('users', {'id': 'analyst@liwen.test', 'email': 'analyst@liwen.test',
                       'name': '语料分析员', 'role': 'analyst',
                       'password_hash': hash_password('Topic2026!')}, insert_only=True)
    resp = client.post('/auth/login', json={'email': 'analyst@liwen.test', 'password': 'Topic2026!'})
    token = resp.json()['access_token']
    return {'Authorization': f'Bearer {token}'}

@pytest.fixture()
def viewer_headers(client):
    repo.put('users', {'id': 'viewer@liwen.test', 'email': 'viewer@liwen.test',
                       'name': '研究观察员', 'role': 'viewer',
                       'password_hash': hash_password('Topic2026!')}, insert_only=True)
    resp = client.post('/auth/login', json={'email': 'viewer@liwen.test', 'password': 'Topic2026!'})
    return {'Authorization': f'Bearer {resp.json()["access_token"]}'}

@pytest.fixture()
def seed_dataset(client, analyst_headers):
    csv_text = 'doc_id,text\n' + '\n'.join(
        f'DOC-{i:03d},"访谈原文第{i}篇，内容编号{i:03d}。"' for i in range(1, 7))
    resp = client.post('/datasets', headers=analyst_headers,
                       json={'name': '修订测试语料', 'source': 'interview_corpus_demo.csv',
                             'csv_text': csv_text})
    assert resp.status_code == 201, resp.text
    return resp.json()
