"""修订发布后，领走版本/已有运行/人工命名必须保持原样的回归测试。"""
from src.utils import repository as repo
from src.utils.identity import now
from src.services.statistics import analyze


def _lexicon_with(terms):
    return {'id': 'lex-test', 'name': '测试词表', 'version': 'v1',
            'terms': terms, 'stopwords': [], 'created_at': now()}


def _dataset_rows():
    return [{'doc_id': f'DOC-{i:03d}', 'text': ' '.join(['公交', '换乘', '车站'])}
            for i in range(1, 7)]


def test_run_and_review_remain_bound_to_original_version(client, analyst_headers):
    # 直接落一份含受控词的语料（v1）
    rows_v1 = _dataset_rows()
    lexicon = _lexicon_with(['公交', '换乘', '车站', '步行', '候车'])
    repo.put('lexicons', lexicon)
    repo.put('datasets', {'id': 'ds-freeze', 'name': '冻结回归语料', 'source': 'x.csv',
                          'rows': rows_v1, 'created_at': now(),
                          'family_id': 'ds-freeze', 'version_seq': 1,
                          'parent_version_id': '', 'revision_batch_id': ''})
    study = {'id': 'study-freeze', 'name': '冻结方案', 'dataset_id': 'ds-freeze',
             'lexicon_id': 'lex-test', 'topic_count': 2, 'max_iter': 50,
             'tolerance': 1e-5, 'seed': 2305, 'created_at': now()}
    repo.put('studies', study)
    run = {'id': 'run-freeze', 'study_id': 'study-freeze', 'status': 'completed',
           'result': analyze(rows_v1, lexicon, study), 'error': '', 'created_at': now()}
    repo.put('runs', run)
    names = {t['id']: f'人工名称{t["id"]}' for t in run['result']['topics']}
    repo.put('reviews', {'id': 'rev-freeze', 'run_id': 'run-freeze',
                         'comment': '修订前完成的人工命名，必须原样保留。',
                         'theme_names': names, 'status': 'approved',
                         'reviewer': 'admin@liwen.test', 'created_at': now()})
    checksum_before = run['result']['checksum']

    # 发布修订 v2：更正一篇 + 新增一篇
    payload = {'batch_id': 'batch-freeze', 'dataset_id': 'ds-freeze',
               'base_version_id': 'ds-freeze',
               'operations': [
                   {'op': 'correct', 'doc_id': 'DOC-001', 'text': '公交 步行 候车 换乘'},
                   {'op': 'add', 'doc_id': 'DOC-007', 'text': '车站 候车 步行'}]}
    resp = client.post('/revisions', headers=analyst_headers, json=payload)
    assert resp.status_code == 200 and resp.json()['batch']['status'] == 'published'

    # 原运行结果（含校验和、文档数、人工命名输入）一字未动
    run_after = repo.get('runs', 'run-freeze')
    assert run_after['result']['checksum'] == checksum_before
    assert run_after['result']['document_count'] == 6
    review_after = repo.get('reviews', 'rev-freeze')
    assert review_after['theme_names'] == names and review_after['status'] == 'approved'

    # 原方案仍指向 v1，未被悄悄改挂到新版本
    assert repo.get('studies', 'study-freeze')['dataset_id'] == 'ds-freeze'

    # 领走版本内容原样，新版本独立承载修订
    v1 = repo.get('datasets', 'ds-freeze')
    assert len(v1['rows']) == 6
    assert next(r for r in v1['rows'] if r['doc_id'] == 'DOC-001')['text'] == rows_v1[0]['text']
    versions = client.get('/datasets/ds-freeze/versions', headers=analyst_headers).json()
    assert [v['version_seq'] for v in versions] == [1, 2]


def test_legacy_dataset_without_version_fields_is_coerced(client, analyst_headers):
    # 模拟初始快照中的旧结构：无 family_id/version_seq
    repo.put('datasets', {'id': 'ds-legacy', 'name': '旧版语料', 'source': 'old.csv',
                          'rows': [{'doc_id': 'A', 'text': '公交 换乘'},
                                   {'doc_id': 'B', 'text': '车站 候车'}],
                          'created_at': now()})
    detail = client.get('/datasets/ds-legacy', headers=analyst_headers).json()
    assert detail['family_id'] == 'ds-legacy' and detail['version_seq'] == 1

    # 旧语料也能作为领走版本发起修订
    payload = {'batch_id': 'batch-legacy', 'dataset_id': 'ds-legacy',
               'base_version_id': 'ds-legacy',
               'operations': [{'op': 'correct', 'doc_id': 'A', 'text': '公交 车站'}]}
    body = client.post('/revisions', headers=analyst_headers, json=payload).json()
    assert body['batch']['status'] == 'published'
    assert body['batch']['base_version_seq'] == 1
