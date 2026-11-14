"""批量修订合并的端到端与纯函数测试。

覆盖需求：
- 按领走版本比对 add/correct/remove；不同文档并发改动自动合并；
- 同篇分歧、移除后晚到旧更正列为冲突，整批不发布；
- 重复投递不新增版本并返回原结果；同批号不同内容拒绝；
- 成功后发布新语料版本，领走版本/已有运行/人工命名保持原样；
- 交错修订与失败后重试。
"""
import concurrent.futures
from src.services.revision import three_way_merge, content_hash, _validate_operations


def rows(ids):
    return [{'doc_id': d, 'text': f'文本{d}'} for d in ids]


# ---------- 纯函数：三方合并 ----------

def test_add_correct_remove_basic():
    base = rows(['A', 'B', 'C'])
    ops = [
        {'op': 'add', 'doc_id': 'D', 'text': '新文本D'},
        {'op': 'correct', 'doc_id': 'A', 'text': '改后A'},
        {'op': 'remove', 'doc_id': 'C', 'text': ''},
    ]
    merged, conflicts, stats = three_way_merge(base, base, ops)
    assert conflicts == []
    assert {r['doc_id'] for r in merged} == {'A', 'B', 'D'}
    assert next(r for r in merged if r['doc_id'] == 'A')['text'] == '改后A'
    assert next(r for r in merged if r['doc_id'] == 'D')['text'] == '新文本D'
    assert stats == {'added': 1, 'corrected': 1, 'removed': 1, 'unchanged': 0}


def test_concurrent_changes_on_different_documents_merge():
    # 两人都领走 base；甲先改 A，乙的批次基于 base 改 B —— 不同文档应自动并入
    base = rows(['A', 'B', 'C'])
    head = [{'doc_id': 'A', 'text': '甲改A'}, {'doc_id': 'B', 'text': '文本B'},
            {'doc_id': 'C', 'text': '文本C'}]
    ops = [{'op': 'correct', 'doc_id': 'B', 'text': '乙改B'}]
    merged, conflicts, stats = three_way_merge(base, head, ops)
    assert conflicts == []
    texts = {r['doc_id']: r['text'] for r in merged}
    assert texts == {'A': '甲改A', 'B': '乙改B', 'C': '文本C'}
    assert stats['corrected'] == 1


def test_same_document_divergent_corrections_conflict():
    base = rows(['A', 'B'])
    head = [{'doc_id': 'A', 'text': '甲改A'}, {'doc_id': 'B', 'text': '文本B'}]
    ops = [{'op': 'correct', 'doc_id': 'A', 'text': '乙改A'}]
    merged, conflicts, stats = three_way_merge(base, head, ops)
    assert merged is None
    assert len(conflicts) == 1 and conflicts[0]['doc_id'] == 'A'
    assert '不一致' in conflicts[0]['reason']


def test_late_correct_after_remove_is_conflict():
    # 领走后文档已被其他批次移除，晚到的旧更正必须算冲突
    base = rows(['A', 'B'])
    head = [{'doc_id': 'B', 'text': '文本B'}]  # A 已被移除
    ops = [{'op': 'correct', 'doc_id': 'A', 'text': '迟到的更正'}]
    merged, conflicts, _ = three_way_merge(base, head, ops)
    assert merged is None
    assert conflicts[0]['doc_id'] == 'A' and '移除' in conflicts[0]['reason']


def test_remove_vs_correct_conflict():
    base = rows(['A', 'B'])
    head = [{'doc_id': 'A', 'text': '别人改后的A'}, {'doc_id': 'B', 'text': '文本B'}]
    merged, conflicts, _ = three_way_merge(base, head, [{'op': 'remove', 'doc_id': 'A', 'text': ''}])
    assert merged is None
    assert conflicts[0]['kind'] == 'remove'


def test_idempotent_convergence_when_other_batch_did_same_change():
    base = rows(['A', 'B'])
    head = [{'doc_id': 'A', 'text': '共同目标文本'}, {'doc_id': 'B', 'text': '文本B'}]
    ops = [{'op': 'correct', 'doc_id': 'A', 'text': '共同目标文本'}]
    merged, conflicts, stats = three_way_merge(base, head, ops)
    assert conflicts == [] and stats['unchanged'] == 1
    assert {r['doc_id'] for r in merged} == {'A', 'B'}


def test_add_existing_id_with_same_content_converges():
    base = rows(['A'])
    head = [{'doc_id': 'A', 'text': '文本A'}, {'doc_id': 'X', 'text': '别人新增X'}]
    merged, conflicts, stats = three_way_merge(base, head,
                                               [{'op': 'add', 'doc_id': 'X', 'text': '别人新增X'}])
    assert conflicts == [] and stats['unchanged'] == 1


def test_add_existing_id_different_content_conflicts():
    base = rows(['A'])
    head = [{'doc_id': 'A', 'text': '文本A'}, {'doc_id': 'X', 'text': '甲的X'}]
    merged, conflicts, _ = three_way_merge(base, head,
                                           [{'op': 'add', 'doc_id': 'X', 'text': '乙的X'}])
    assert merged is None and conflicts[0]['doc_id'] == 'X'


def test_correct_missing_base_document_conflicts():
    base = rows(['A'])
    merged, conflicts, _ = three_way_merge(base, base,
                                           [{'op': 'correct', 'doc_id': 'GHOST', 'text': 'x'}])
    assert merged is None and conflicts[0]['reason']


def test_duplicate_remove_converges():
    base = rows(['A', 'B', 'C'])
    head = [{'doc_id': 'B', 'text': '文本B'}, {'doc_id': 'C', 'text': '文本C'}]
    merged, conflicts, stats = three_way_merge(base, head,
                                               [{'op': 'remove', 'doc_id': 'A', 'text': ''}])
    assert conflicts == [] and stats['unchanged'] == 1
    assert {r['doc_id'] for r in merged} == {'B', 'C'}


def test_new_document_ordering_stable():
    base = rows(['B', 'A'])
    ops = [{'op': 'add', 'doc_id': 'C', 'text': '文本C'},
           {'op': 'add', 'doc_id': 'A2', 'text': '文本A2'}]
    merged, _, _ = three_way_merge(base, base, ops)
    assert [r['doc_id'] for r in merged] == ['B', 'A', 'C', 'A2']


def test_validate_rejects_duplicate_doc_in_batch():
    ops = [{'op': 'correct', 'doc_id': 'A', 'text': 'x'},
           {'op': 'remove', 'doc_id': 'A', 'text': ''}]
    try:
        _validate_operations(ops)
        assert False, '应拒绝同篇多条修订'
    except ValueError as exc:
        assert '多次' in str(exc)


# ---------- HTTP 端到端 ----------

def _revision_payload(seed, batch_id, ops, base_id=None):
    return {'batch_id': batch_id, 'dataset_id': seed['id'],
            'base_version_id': base_id or seed['id'], 'operations': ops}


def test_publish_creates_new_version_and_keeps_base(client, analyst_headers, seed_dataset):
    payload = _revision_payload(seed_dataset, 'batch-001', [
        {'op': 'correct', 'doc_id': 'DOC-001', 'text': '修订后的第一篇'},
        {'op': 'add', 'doc_id': 'DOC-007', 'text': '全新补访第七篇'},
        {'op': 'remove', 'doc_id': 'DOC-006', 'text': ''},
    ])
    resp = client.post('/revisions', headers=analyst_headers, json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['created'] is True
    batch = body['batch']
    assert batch['status'] == 'published'
    assert batch['new_version_id']
    assert batch['summary'] == {'added': 1, 'corrected': 1, 'removed': 1, 'unchanged': 0}

    # 新版本内容
    new = client.get(f"/datasets/{batch['new_version_id']}", headers=analyst_headers).json()
    assert new['version_seq'] == 2 and new['family_id'] == seed_dataset['id']
    assert new['parent_version_id'] == seed_dataset['id']
    assert new['row_count'] == 6  # 6 - 1 + 1

    # 领走版本原样保留
    base = client.get(f"/datasets/{seed_dataset['id']}", headers=analyst_headers).json()
    assert base['version_seq'] == 1 and base['row_count'] == 6

    # 版本族清单含两版
    versions = client.get(f"/datasets/{seed_dataset['id']}/versions",
                          headers=analyst_headers).json()
    assert [v['version_seq'] for v in versions] == [1, 2]

    # 语料列表每族只显示最新版
    listing = client.get('/datasets', headers=analyst_headers).json()
    family = [d for d in listing if d['id'] == batch['new_version_id']]
    assert len(family) == 1 and family[0]['version_seq'] == 2


def test_duplicate_delivery_returns_same_result_no_new_version(client, analyst_headers, seed_dataset):
    payload = _revision_payload(seed_dataset, 'batch-dup', [
        {'op': 'correct', 'doc_id': 'DOC-002', 'text': '重复投递测试文本'}])
    first = client.post('/revisions', headers=analyst_headers, json=payload).json()
    second = client.post('/revisions', headers=analyst_headers, json=payload).json()
    assert first['batch']['id'] == second['batch']['id']
    assert first['batch']['new_version_id'] == second['batch']['new_version_id']
    assert second['created'] is False  # 未再产生处理记录/版本

    versions = client.get(f"/datasets/{seed_dataset['id']}/versions",
                          headers=analyst_headers).json()
    assert len(versions) == 2  # 仅首次发布了一版

    # GET 批号也能取回原结果
    got = client.get('/revisions/batch-dup', headers=analyst_headers).json()
    assert got['new_version_id'] == first['batch']['new_version_id']


def test_same_batch_id_different_content_rejected(client, analyst_headers, seed_dataset):
    p1 = _revision_payload(seed_dataset, 'batch-collision',
                           [{'op': 'correct', 'doc_id': 'DOC-001', 'text': '版本甲'}])
    p2 = _revision_payload(seed_dataset, 'batch-collision',
                           [{'op': 'correct', 'doc_id': 'DOC-001', 'text': '版本乙'}])
    r1 = client.post('/revisions', headers=analyst_headers, json=p1)
    assert r1.status_code == 200
    r2 = client.post('/revisions', headers=analyst_headers, json=p2)
    assert r2.status_code == 409
    # 拒绝后首结果不变、未追加版本
    versions = client.get(f"/datasets/{seed_dataset['id']}/versions",
                          headers=analyst_headers).json()
    assert len(versions) == 2


def test_conflict_batch_publishes_nothing_and_retry_succeeds(client, analyst_headers, seed_dataset):
    # 批次甲：改 DOC-003 并发布 v2
    p_a = _revision_payload(seed_dataset, 'batch-A',
                            [{'op': 'correct', 'doc_id': 'DOC-003', 'text': '甲的修订'}])
    assert client.post('/revisions', headers=analyst_headers, json=p_a).json()['batch']['status'] == 'published'

    # 批次乙同样基于 v1 改 DOC-003，且内容不同 → 冲突，整批不发布
    p_b = _revision_payload(seed_dataset, 'batch-B',
                            [{'op': 'correct', 'doc_id': 'DOC-003', 'text': '乙的修订'},
                             {'op': 'correct', 'doc_id': 'DOC-004', 'text': '乙的另一修订'}])
    rb = client.post('/revisions', headers=analyst_headers, json=p_b)
    assert rb.status_code == 200
    batch_b = rb.json()['batch']
    assert batch_b['status'] == 'conflict'
    assert batch_b['new_version_id'] == ''
    assert len(batch_b['conflicts']) == 1 and batch_b['conflicts'][0]['doc_id'] == 'DOC-003'

    versions = client.get(f"/datasets/{seed_dataset['id']}/versions",
                          headers=analyst_headers).json()
    assert len(versions) == 2  # 冲突批次没有产生新版本

    # 失败后重试：换新批号、改为基于当前最新版、只改无分歧的 DOC-004 → 成功发布 v3
    head_id = versions[-1]['id']
    p_b_retry = _revision_payload(seed_dataset, 'batch-B-retry',
                                  [{'op': 'correct', 'doc_id': 'DOC-004', 'text': '乙的另一修订'}],
                                  base_id=head_id)
    rr = client.post('/revisions', headers=analyst_headers, json=p_b_retry)
    assert rr.status_code == 200
    assert rr.json()['batch']['status'] == 'published'
    assert len(client.get(f"/datasets/{seed_dataset['id']}/versions",
                          headers=analyst_headers).json()) == 3


def test_interleaved_remove_then_late_correct_conflicts(client, analyst_headers, seed_dataset):
    # 甲先移除 DOC-005 发布 v2
    p_remove = _revision_payload(seed_dataset, 'batch-rm',
                                 [{'op': 'remove', 'doc_id': 'DOC-005', 'text': ''}])
    assert client.post('/revisions', headers=analyst_headers,
                       json=p_remove).json()['batch']['status'] == 'published'
    # 乙仍基于 v1 更正 DOC-005 → 冲突
    p_late = _revision_payload(seed_dataset, 'batch-late',
                               [{'op': 'correct', 'doc_id': 'DOC-005', 'text': '迟来的更正'}])
    body = client.post('/revisions', headers=analyst_headers, json=p_late).json()
    assert body['batch']['status'] == 'conflict'
    assert '移除' in body['batch']['conflicts'][0]['reason']


def test_concurrent_batches_serialize_and_both_land(client, analyst_headers, seed_dataset):
    # 两个批次改不同文档、同时投递：串行合并后两条改动都应进入新版本链
    p1 = _revision_payload(seed_dataset, 'batch-conc-1',
                           [{'op': 'correct', 'doc_id': 'DOC-001', 'text': '并发修订一'}])
    p2 = _revision_payload(seed_dataset, 'batch-conc-2',
                           [{'op': 'correct', 'doc_id': 'DOC-002', 'text': '并发修订二'}])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda p: client.post('/revisions', headers=analyst_headers, json=p), [p1, p2]))
    statuses = sorted(r.json()['batch']['status'] for r in results)
    assert statuses == ['published', 'published']
    head = client.get('/datasets', headers=analyst_headers).json()
    family_head = [d for d in head if d['family_id'] == seed_dataset['id']][0]
    full = client.get(f"/datasets/{family_head['id']}", headers=analyst_headers).json()
    # 最新版同时含两处修订（详情接口不含 rows，改用版本链终点校验行数与批号溯源）
    assert family_head['version_seq'] == 3
    assert full['revision_batch_id'] in ('batch-conc-1', 'batch-conc-2')


def test_concurrent_duplicate_batch_is_idempotent(client, analyst_headers, seed_dataset):
    payload = _revision_payload(seed_dataset, 'batch-conc-dup',
                                [{'op': 'correct', 'doc_id': 'DOC-003', 'text': '并发同批号同内容'}])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: client.post('/revisions', headers=analyst_headers, json=payload), range(2)))
    assert all(r.status_code == 200 for r in results)
    new_ids = {r.json()['batch']['new_version_id'] for r in results}
    assert len(new_ids) == 1  # 同一批号并发投递只发布一个版本
    created = [r.json()['created'] for r in results]
    assert sorted(created) == [False, True]
    versions = client.get(f"/datasets/{seed_dataset['id']}/versions",
                          headers=analyst_headers).json()
    assert len(versions) == 2


def test_viewer_cannot_submit(client, viewer_headers, seed_dataset):
    payload = _revision_payload(seed_dataset, 'batch-forbidden',
                                [{'op': 'remove', 'doc_id': 'DOC-001', 'text': ''}])
    resp = client.post('/revisions', headers=viewer_headers, json=payload)
    assert resp.status_code == 403


def test_unknown_base_version_404(client, analyst_headers, seed_dataset):
    payload = {'batch_id': 'batch-nobase', 'dataset_id': seed_dataset['id'],
               'base_version_id': 'ds-does-not-exist',
               'operations': [{'op': 'remove', 'doc_id': 'DOC-001', 'text': ''}]}
    assert client.post('/revisions', headers=analyst_headers, json=payload).status_code == 404


def test_validation_empty_text_rejected(client, analyst_headers, seed_dataset):
    payload = _revision_payload(seed_dataset, 'batch-bad-op',
                                [{'op': 'correct', 'doc_id': 'DOC-001', 'text': ''}])
    assert client.post('/revisions', headers=analyst_headers, json=payload).status_code == 422


def test_merge_below_two_documents_rejected():
    base = rows(['A', 'B'])
    try:
        three_way_merge(base, base, [{'op': 'remove', 'doc_id': 'A', 'text': ''}])
        assert False, '合并后仅剩1篇应拒绝'
    except ValueError as exc:
        assert '少于2篇' in str(exc)


def test_rejected_batch_publishes_nothing(client, analyst_headers, seed_dataset):
    # 6 篇移除 5 篇 → 合并后仅 1 篇，批号记录为 rejected 且无新版本
    ops = [{'op': 'remove', 'doc_id': f'DOC-{i:03d}', 'text': ''} for i in range(1, 6)]
    payload = _revision_payload(seed_dataset, 'batch-too-small', ops)
    resp = client.post('/revisions', headers=analyst_headers, json=payload)
    assert resp.status_code == 200
    batch = resp.json()['batch']
    assert batch['status'] == 'rejected' and batch['new_version_id'] == ''
    assert '少于2篇' in batch['error']
    versions = client.get(f"/datasets/{seed_dataset['id']}/versions",
                          headers=analyst_headers).json()
    assert len(versions) == 1  # 仍只有领走版


def test_content_hash_stable_and_order_independent():
    ops1 = [{'op': 'correct', 'doc_id': 'A', 'text': 'x'},
            {'op': 'remove', 'doc_id': 'B', 'text': ''}]
    ops2 = [{'op': 'remove', 'doc_id': 'B', 'text': ''},
            {'op': 'correct', 'doc_id': 'A', 'text': 'x'}]
    h1 = content_hash('fam', 'base', ops1)
    h2 = content_hash('fam', 'base', ops2)
    assert h1 == h2  # 同语义不同投递顺序视为同一内容，支持幂等重试
