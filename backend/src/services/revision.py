"""访谈语料批量修订合并。

多人各自领走同一版本（base）做新增/更正/移除后，通过本服务按批合并：
- 不同文档的并发改动自动并入，成功时发布新的语料版本（版本族内追加，不改写老版本）；
- 同一文档出现无法自动调和的分歧时记为冲突，整批不发布、不产生新版本；
- 批号为自然键：重复投递且内容一致时原样返回首次结果，不新增版本；
  批号相同但内容不同一律拒绝（409）。

合并是基于 领走版本(base) → 当前最新版(head) → 批后(theirs) 的三方合并，
因此“移除后晚到的旧更正”等交错修订也能被识别为冲突而不是静默覆盖。
"""
import hashlib
import json
from dataclasses import asdict
from fastapi import HTTPException
from src.models.entities import CorpusDataset, RevisionBatch
from src.utils import repository as repo
from src.utils.identity import new_id, now
from src.constants.domain import MAX_DOCUMENTS, MAX_CHARACTERS


def _row_map(rows):
    return {r['doc_id']: r['text'] for r in rows}


def content_hash(family_id, base_version_id, operations):
    """批号内容指纹：只取决于合并语义相关字段，与投递次数、操作顺序无关。"""
    canonical = sorted(
        ({'op': o['op'], 'doc_id': o['doc_id'], 'text': o.get('text', '')} for o in operations),
        key=lambda o: o['doc_id'])
    payload = json.dumps(
        {'family_id': family_id, 'base_version_id': base_version_id, 'operations': canonical},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _validate_operations(operations):
    seen = set()
    normalized = []
    for n, op in enumerate(operations, 1):
        doc_id = op['doc_id'].strip()
        kind = op['op']
        text = op.get('text', '').strip()
        if not doc_id:
            raise ValueError(f'第{n}条修订缺少doc_id')
        if doc_id in seen:
            raise ValueError(f'同一批号内 doc_id={doc_id} 出现多次，请每篇至多一条修订')
        seen.add(doc_id)
        if kind in ('add', 'correct'):
            if not 1 <= len(text) <= MAX_CHARACTERS:
                raise ValueError(f'第{n}条修订（{kind} {doc_id}）文本须为1—{MAX_CHARACTERS}字')
        elif kind == 'remove':
            text = ''
        else:  # pragma: no cover - 由 pydantic Literal 拦截
            raise ValueError(f'不支持的修订类型：{kind}')
        normalized.append({'op': kind, 'doc_id': doc_id, 'text': text})
    return normalized


def three_way_merge(base_rows, head_rows, operations):
    """纯函数三方合并。

    base_rows/head_rows：领走版本与当前最新版本的行（[{doc_id,text}]）。
    返回 (merged_rows, conflicts, stats)。conflicts 非空表示整批不可发布。
    """
    base, head = _row_map(base_rows), _row_map(head_rows)
    merged = dict(head)
    conflicts = []
    stats = {'added': 0, 'corrected': 0, 'removed': 0, 'unchanged': 0}

    for op in operations:
        doc_id, kind, their_text = op['doc_id'], op['op'], op['text']
        in_base, in_head = doc_id in base, doc_id in head
        base_text, head_text = base.get(doc_id), head.get(doc_id)
        head_changed = in_base != in_head or (in_base and base_text != head_text)

        if kind == 'add':
            if in_base:
                conflicts.append({'doc_id': doc_id, 'kind': 'add',
                                  'reason': '领走版本已存在该文档，不能新增',
                                  'base_text': base_text, 'head_text': head_text, 'submitted_text': their_text})
                continue
            if in_head:
                # 领走后该文档已由别的批次新增：内容一致才视为同一新增，否则冲突
                if head_text == their_text:
                    stats['unchanged'] += 1
                else:
                    conflicts.append({'doc_id': doc_id, 'kind': 'add',
                                      'reason': '该文档已被其他批次新增，新增内容不一致',
                                      'base_text': None, 'head_text': head_text, 'submitted_text': their_text})
                continue
            merged[doc_id] = their_text
            stats['added'] += 1

        elif kind == 'correct':
            if not in_base:
                conflicts.append({'doc_id': doc_id, 'kind': 'correct',
                                  'reason': '领走版本中不存在该文档，无法更正',
                                  'base_text': None, 'head_text': head_text, 'submitted_text': their_text})
                continue
            if not in_head:
                # 文档在领走之后已被移除：晚到的旧更正与移除分歧
                conflicts.append({'doc_id': doc_id, 'kind': 'correct',
                                  'reason': '文档已在其他批次中移除，晚到的更正与移除冲突',
                                  'base_text': base_text, 'head_text': None, 'submitted_text': their_text})
                continue
            if base_text == their_text:
                # 提交内容与领走原文相同：没有实际改动
                stats['unchanged'] += 1
                continue
            if head_changed:
                if head_text == their_text:
                    # 别的批次已经改成同样的结果，幂等收敛
                    stats['unchanged'] += 1
                else:
                    conflicts.append({'doc_id': doc_id, 'kind': 'correct',
                                      'reason': '该文档已被其他批次改动，双方更正内容不一致',
                                      'base_text': base_text, 'head_text': head_text, 'submitted_text': their_text})
                continue
            merged[doc_id] = their_text
            stats['corrected'] += 1

        else:  # remove
            if not in_base:
                conflicts.append({'doc_id': doc_id, 'kind': 'remove',
                                  'reason': '领走版本中不存在该文档，无法移除',
                                  'base_text': None, 'head_text': head_text, 'submitted_text': ''})
                continue
            if not in_head:
                # 已被其他批次移除：重复移除收敛为无操作
                stats['unchanged'] += 1
                continue
            if head_changed:
                conflicts.append({'doc_id': doc_id, 'kind': 'remove',
                                  'reason': '文档已被其他批次更正，移除与更正冲突',
                                  'base_text': base_text, 'head_text': head_text, 'submitted_text': ''})
                continue
            del merged[doc_id]
            stats['removed'] += 1

    if conflicts:
        return None, conflicts, stats

    # 保持稳定顺序：沿用 head 中尚存文档的顺序，新增文档按其在批次中的出现顺序追加
    order = [r['doc_id'] for r in head_rows if r['doc_id'] in merged]
    existing = set(order)
    for op in operations:
        if op['doc_id'] in merged and op['doc_id'] not in existing:
            order.append(op['doc_id'])
            existing.add(op['doc_id'])
    merged_rows = [{'doc_id': d, 'text': merged[d]} for d in order]
    if len(merged_rows) < 2:
        raise ValueError('合并后有效文档少于2篇，拒绝发布该修订版本')
    if len(merged_rows) > MAX_DOCUMENTS:
        raise ValueError(f'合并后文档超过{MAX_DOCUMENTS}篇上限')
    return merged_rows, [], stats


def _family_versions(tx, family_id):
    versions = []
    for d in tx.all('datasets'):
        d.setdefault('family_id', d['id'])
        d.setdefault('version_seq', 1)
        if d['family_id'] == family_id:
            versions.append(d)
    versions.sort(key=lambda d: d['version_seq'])
    return versions


def submit_batch(data, user):
    operations = _validate_operations([o.model_dump() for o in data.operations])

    with repo.transaction() as tx:
        base = tx.find('datasets', data.base_version_id)
        if base is None:
            raise HTTPException(404, '领走版本不存在')
        family_id = base.get('family_id') or base['id']
        base.setdefault('version_seq', 1)
        if data.dataset_id not in (family_id, base['id']):
            raise HTTPException(404, '领走版本不属于该语料')

        fingerprint = content_hash(family_id, data.base_version_id, operations)

        # 批号自然键：重复投递幂等，同号异内容拒绝
        existing = tx.find('revision_batches', data.batch_id)
        if existing is not None:
            if existing['content_hash'] != fingerprint:
                raise HTTPException(409, '批号已被使用且提交内容不同，拒绝覆盖；请使用新批号')
            return existing, False

        versions = _family_versions(tx, family_id)
        head = versions[-1]
        try:
            merged_rows, conflicts, stats = three_way_merge(base['rows'], head['rows'], operations)
        except ValueError as exc:
            batch = _persist(tx, data.batch_id, family_id, base, head, operations, fingerprint,
                             status='rejected', conflicts=[], stats={}, error=str(exc), user=user)
            return batch, True

        if conflicts:
            batch = _persist(tx, data.batch_id, family_id, base, head, operations, fingerprint,
                             status='conflict', conflicts=conflicts, stats=stats, error='', user=user)
            return batch, True

        new_version = CorpusDataset(
            id=new_id('ds'), name=head['name'], source=head['source'], rows=merged_rows,
            created_at=now(), family_id=family_id, version_seq=head['version_seq'] + 1,
            parent_version_id=head['id'], revision_batch_id=data.batch_id)
        tx.put('datasets', new_version)
        batch = _persist(tx, data.batch_id, family_id, base, head, operations, fingerprint,
                         status='published', conflicts=[], stats=stats, error='', user=user,
                         new_version=new_version)
        return batch, True


def _persist(tx, batch_id, family_id, base, head, operations, fingerprint,
             status, conflicts, stats, error, user, new_version=None):
    batch = RevisionBatch(
        id=batch_id, family_id=family_id, base_version_id=base['id'],
        base_version_seq=base['version_seq'], operations=operations, content_hash=fingerprint,
        status=status, submitted_by=user.get('email', ''),
        head_version_id=head['id'], head_version_seq=head['version_seq'],
        new_version_id=new_version.id if new_version else '',
        conflicts=conflicts,
        summary=stats, error=error, created_at=now(), resolved_at=now())
    tx.put('revision_batches', batch, insert_only=True)
    return asdict(batch)


def get_batch(batch_id):
    return repo.get('revision_batches', batch_id)


def list_batches(family_id=None):
    rows = repo.all_rows('revision_batches')
    if family_id:
        rows = [r for r in rows if r['family_id'] == family_id]
    rows.sort(key=lambda r: r['created_at'])
    return rows
