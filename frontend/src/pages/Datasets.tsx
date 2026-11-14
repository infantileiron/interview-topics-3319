import {useState} from 'react';
import * as api from '../api/datasets';
import * as revisionsApi from '../api/revisions';
import {useResource} from '../hooks/useResource';
import {refreshResources} from '../stores/resources';
import PageTitle from '../components/common/PageTitle';
import Notice from '../components/common/Notice';
import type {User,Dataset} from '../types/entities';

const statusText:Record<string,string>={published:'已发布新版本',conflict:'冲突·整批未发布',rejected:'拒绝·未发布'};

export default function Datasets({user}:{user:User}){
 const{data,error}=useResource(api.list,[] as Dataset[]);
 const{data:batches}=useResource(revisionsApi.list,[] as revisionsApi.RevisionBatch[]);
 const[name,setName]=useState('补充匿名访谈语料'),[source,setSource]=useState('interview_corpus_202611.csv'),[csv,setCsv]=useState(''),[message,setMessage]=useState('');
 const[selected,setSelected]=useState(''),[batchId,setBatchId]=useState(''),[baseVersion,setBaseVersion]=useState(''),[opsText,setOpsText]=useState(''),[lastResult,setLastResult]=useState<revisionsApi.RevisionBatch|null>(null);
 const[versionRows,setVersionRows]=useState<Dataset[]>([]);
 const editable=user.role!=='viewer';
 const target=data.find(d=>d.id===selected||d.family_id===selected);
 const openVersions=async(familyId:string)=>{setSelected(familyId);setBaseVersion('');setVersionRows(await revisionsApi.versions(familyId));};
 const submitRevision=async(e:React.FormEvent)=>{
  e.preventDefault();setLastResult(null);
  if(!target){setMessage('请先选择要修订的语料');return;}
  let operations:revisionsApi.RevisionOp[];
  try{
   const parsed=JSON.parse(opsText);
   if(!Array.isArray(parsed)||!parsed.length)throw new Error('修订须为非空数组');
   operations=parsed.map((p:any)=>({op:p.op,doc_id:String(p.doc_id??'').trim(),text:String(p.text??'')}));
   if(operations.some(p=>!['add','correct','remove'].includes(p.op)||!p.doc_id))throw new Error('每条修订需含合法 op 与 doc_id');
  }catch(err:any){setMessage('修订JSON解析失败：'+err.message);return;}
  try{
   const resp=await revisionsApi.submit({batch_id:batchId.trim(),dataset_id:target.family_id,base_version_id:baseVersion||target.id,operations});
   setLastResult(resp.batch);setMessage(resp.batch.status==='published'?'':`批号 ${resp.batch.id}：${statusText[resp.batch.status]}`);
   setOpsText('');refreshResources();
   setVersionRows(await revisionsApi.versions(target.family_id));
  }catch(err:any){setMessage(err.message);}
 };
 return <>
  <PageTitle chapter="02" title="留下原文，也公开被隔离的文档" description="只接收doc_id与匿名模拟文本。多人修订按领走版本批量三方合并：不同文档自动并入，同篇分歧整批不发布；成功只追加新语料版本，领走版本、已有运行与人工命名保持原样。"/>
  <Notice error={error||message}/>
  <section className="paper table-scroll">
   <table><thead><tr><th>语料文件</th><th>版本</th><th>文档数</th><th>字符数</th><th>修订</th></tr></thead>
   <tbody>{data.map(d=><tr key={d.id}><td><b>{d.name}</b><small>{d.source}</small></td><td>v{d.version_seq}</td><td>{d.row_count}</td><td>{d.characters}</td>{editable&&<td><button onClick={()=>openVersions(d.family_id)}>修订 / 版本链</button></td>}</tr>)}</tbody></table>
  </section>
  {editable&&target&&<section className="paper">
   <h2>批量修订合并 · {target.name}</h2>
   {versionRows.length>0&&<table className="table-scroll"><thead><tr><th>版本</th><th>文档数</th><th>来源批号</th></tr></thead><tbody>{versionRows.map(v=><tr key={v.id}><td>v{v.version_seq}（{v.id}）</td><td>{v.row_count}</td><td>{v.revision_batch_id||'原始导入'}</td></tr>)}</tbody></table>}
   <form className="form-grid" onSubmit={submitRevision}>
    <label>批号（自然键，重复投递同内容返回原结果）<input aria-label="批号" value={batchId} onChange={e=>setBatchId(e.target.value)} placeholder="rev-202611-001" required/></label>
    <label>领走版本<select aria-label="领走版本" value={baseVersion||target.id} onChange={e=>setBaseVersion(e.target.value)}>{versionRows.map(v=><option key={v.id} value={v.id}>v{v.version_seq} · {v.id}</option>)}</select></label>
    <label className="wide">修订数组（op: add/correct/remove；remove 无需 text）<textarea aria-label="修订JSON" rows={7} value={opsText} onChange={e=>setOpsText(e.target.value)} placeholder={'[\n  {"op":"correct","doc_id":"LW-0001","text":"修订后的匿名文本"},\n  {"op":"add","doc_id":"LW-0481","text":"补访文本"},\n  {"op":"remove","doc_id":"LW-0007"}\n]'} required/></label>
    <button className="primary">按领走版本比对并合并</button>
   </form>
   {lastResult&&<div className="notice" role="status">
    <b>批号 {lastResult.id}：{statusText[lastResult.status]}</b>
    {lastResult.status==='published'&&<small>新增 {lastResult.summary.added} · 更正 {lastResult.summary.corrected} · 移除 {lastResult.summary.removed} · 无变化收敛 {lastResult.summary.unchanged}；新版本 {lastResult.new_version_id}</small>}
    {lastResult.status==='rejected'&&<small>{lastResult.error}</small>}
    {lastResult.conflicts.length>0&&<table className="table-scroll"><thead><tr><th>doc_id</th><th>类型</th><th>冲突原因</th></tr></thead><tbody>{lastResult.conflicts.map(c=><tr key={c.doc_id}><td>{c.doc_id}</td><td>{c.kind}</td><td>{c.reason}</td></tr>)}</tbody></table>}
   </div>}
  </section>}
  {editable&&batches.length>0&&<section className="paper table-scroll">
   <h2>修订批号台账</h2>
   <table><thead><tr><th>批号</th><th>状态</th><th>领走版</th><th>合并时最新版</th><th>发布版本</th><th>冲突数</th></tr></thead>
   <tbody>{batches.map(b=><tr key={b.id}><td><b>{b.id}</b><small>{b.submitted_by}</small></td><td>{statusText[b.status]}</td><td>v{b.base_version_seq}</td><td>v{b.head_version_seq}</td><td>{b.new_version_id||'—'}</td><td>{b.conflicts.length}</td></tr>)}</tbody></table>
  </section>}
  {editable&&<form className="paper form-grid" onSubmit={async e=>{e.preventDefault();try{await api.create({name,source,csv_text:csv});setMessage('');setCsv('');refreshResources()}catch(e:any){setMessage(e.message)}}}>
   <h2>导入新语料（独立版本族）</h2>
   <label>数据集名称<input aria-label="数据集名称" value={name} onChange={e=>setName(e.target.value)}/></label>
   <label>CSV文件<input type="file" accept=".csv" onChange={async e=>{const f=e.target.files?.[0];if(f){if(f.size>1800000){setMessage('CSV不能超过1.8MB');return}setSource(f.name);setCsv(await f.text())}}}/></label>
   <label className="wide">语料内容<textarea value={csv} onChange={e=>setCsv(e.target.value)} placeholder="doc_id,text"/></label>
   <button className="primary">校验并导入语料</button>
  </form>}
 </>;
}
