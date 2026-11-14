import {request} from './client';
export type RevisionOp={op:'add'|'correct'|'remove';doc_id:string;text?:string};
export type RevisionBatchInput={
 batch_id:string;dataset_id:string;base_version_id:string;operations:RevisionOp[];
};
export type RevisionConflict={doc_id:string;kind:string;reason:string;base_text:string|null;head_text:string|null;submitted_text:string};
export type RevisionBatch={
 id:string;family_id:string;base_version_id:string;base_version_seq:number;
 operations:RevisionOp[];content_hash:string;status:'published'|'conflict'|'rejected';
 submitted_by:string;head_version_id:string;head_version_seq:number;new_version_id:string;
 conflicts:RevisionConflict[];summary:{added:number;corrected:number;removed:number;unchanged:number};
 error:string;created_at:string;resolved_at:string;
};
export const submit=(data:RevisionBatchInput)=>request<{created:boolean;batch:RevisionBatch}>('/revisions','POST',data);
export const list=(family_id?:string)=>request<RevisionBatch[]>(family_id?`/revisions?family_id=${encodeURIComponent(family_id)}`:'/revisions');
export const versions=(family_id:string)=>request<any[]>(`/datasets/${encodeURIComponent(family_id)}/versions`);
