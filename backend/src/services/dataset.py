import csv,io
from dataclasses import asdict
from fastapi import HTTPException
from src.models.entities import CorpusDataset
from src.utils import repository as repo
from src.utils.identity import new_id,now
from src.constants.domain import MAX_DOCUMENTS,MAX_CHARACTERS

def _coerce_version(item):
 # 兼容初始快照里没有版本族字段的旧记录
 item.setdefault('family_id', item['id'])
 item.setdefault('version_seq', 1)
 item.setdefault('parent_version_id', '')
 item.setdefault('revision_batch_id', '')
 return item

def summary(item):
 _coerce_version(item)
 return {k:v for k,v in item.items() if k!='rows'}|{
  'row_count':len(item['rows']),'characters':sum(len(r['text']) for r in item['rows'])}

def list_datasets():
 # 每个版本族只展示最新版本；历史版本通过 detail 与版本清单接口取得
 latest={}
 for item in repo.all_rows('datasets'):
  item=_coerce_version(item)
  cur=latest.get(item['family_id'])
  if cur is None or item['version_seq']>cur['version_seq']:latest[item['family_id']]=item
 return [summary(x) for x in sorted(latest.values(),key=lambda x:x['created_at'])]

def list_versions(family_id):
 items=[_coerce_version(x) for x in repo.all_rows('datasets') if (_coerce_version(x).get('family_id')==family_id or x['id']==family_id)]
 if not items:raise HTTPException(404,'语料版本族不存在')
 items.sort(key=lambda x:x['version_seq'])
 return [summary(x) for x in items]

def detail(id):
 return summary(repo.get('datasets',id))

def detail_full(id):
 return _coerce_version(repo.get('datasets',id))

def rename(id,data):
 item=repo.get('datasets',id);item['name']=data.name;repo.put('datasets',item);return summary(item)

def create(data):
 reader=csv.DictReader(io.StringIO(data.csv_text.lstrip('﻿')))
 if not {'doc_id','text'}.issubset(reader.fieldnames or []):raise ValueError('CSV需要doc_id,text，内容须为匿名模拟访谈')
 rows=[];seen=set()
 for n,r in enumerate(reader,2):
  if n>MAX_DOCUMENTS+1:raise ValueError('最多600篇文档')
  id=(r.get('doc_id') or '').strip();text=(r.get('text') or '').strip()
  if not id or id in seen or not 1<=len(text)<=MAX_CHARACTERS:raise ValueError(f'第{n}行编号/文本为空、重复或超过2000字')
  seen.add(id);rows.append({'doc_id':id,'text':text})
 if len(rows)<2:raise ValueError('至少2篇文档')
 item=CorpusDataset(new_id('ds'),data.name,data.source,rows,now());repo.put('datasets',item);return summary(asdict(item))
