import {backendId, request, ApiError} from './client'

export interface SqlColumn {name:string; type:string; physical_type:string; role:string; description:string; key:boolean; time:boolean; sensitive:boolean; queryable:boolean}
export interface SqlDataset {dataset_id:number; name:string; alias:string; queryable:boolean; freshness:string; reason:string; row_count:number|null; columns:SqlColumn[]; updated_at:string|null; state_id:number|null;gold_run_id:number|null;profile_id:number|null}
export interface SqlRelationship {relationship_id:number; version:number; parent:{dataset_id:number; columns:string[]}; child:{dataset_id:number; columns:string[]}; cardinality:string}
export interface SqlCatalog {workspace_id:number; workspace_name:string; datasets:SqlDataset[]; relationships:SqlRelationship[]; capabilities:{dialect:string; functions:string[]; limits:{output_rows:number; seconds:number; text_length:number; history:number}; join_policy:string; sensitivity_policy:string; result_policy:string}}
export interface SqlDiagnostic {code:string; message:string; line?:number; column?:number}
export type SqlCell=number|string|boolean|null
export interface SqlExecution {workspace_id:number; execution_id:number; sql_text?:string; normalized_sql?:string; status:'SUCCEEDED'|'BLOCKED'|'FAILED'|'TIMED_OUT'; diagnostics:SqlDiagnostic[]; columns?:{name:string; type:string}[]; rows:SqlCell[][]; row_count?:number; truncated?:boolean; duration_ms:number; freshness:string; historical?:boolean; sources:{dataset_id:number; name:string; alias:string; state_id:number; gold_run_id:number}[]; relationships:SqlRelationship[]; executed_at:string; engine?:string; engine_version?:string; trust_label:string}
export interface SavedSql {query_id:number; workspace_id:number; name:string; description:string; sql_text:string; version:number; updated_at:string; last_run?:string|null}
export interface SqlValidation {workspace_id:number; status:string; normalized_sql?:string; formatted_sql?:string; diagnostics:SqlDiagnostic[]; warnings?:string[]}
const base=(ws:string)=>`/workspaces/${backendId(ws)}/sql`
async function sqlRequest<T>(path:string,options:RequestInit={}) {
  try{return await request<T>(path,options)}catch(error){
    if(error instanceof ApiError){
      const message=error.status===409?'Saved query changed or the saved-query limit was reached. Reopen the query and check saved queries before retrying.'
        :error.status===429?'SQL execution is busy. Wait for a running query to finish, then retry.'
        :error.status===400||error.status===422?'The SQL request fields are invalid. Check the query or Builder selections.'
        :error.status===404?'The workspace, saved query or execution is unavailable. Refresh this workspace.'
        :undefined
      if(message)throw new ApiError(message,error.status)
    }
    throw error
  }
}
async function scoped<T extends {workspace_id:number}>(ws:string,suffix:string,options:RequestInit={}) {
  const value=await sqlRequest<T>(base(ws)+suffix,options)
  if(value.workspace_id!==backendId(ws))throw new ApiError('SQL data belongs to another workspace.')
  return value
}
async function list<T extends {workspace_id:number}>(ws:string,suffix:string,signal?:AbortSignal) {
  const value=await sqlRequest<T[]>(base(ws)+suffix,{signal})
  if(value.some(v=>v.workspace_id!==backendId(ws)))throw new ApiError('SQL list belongs to another workspace.')
  return value
}
const json=(body:unknown,method='POST',signal?:AbortSignal)=>({method,signal,body:JSON.stringify(body)})
export const sqlCatalog=(ws:string,signal?:AbortSignal)=>scoped<SqlCatalog>(ws,'/catalog',{signal})
export const sqlQueries=(ws:string,signal?:AbortSignal)=>list<SavedSql>(ws,'/queries',signal)
export const sqlHistory=(ws:string,signal?:AbortSignal)=>list<SqlExecution>(ws,'/executions',signal)
export const validateSql=(ws:string,sql_text:string,signal?:AbortSignal)=>scoped<SqlValidation>(ws,'/validate',json({sql_text},'POST',signal))
export const executeSql=(ws:string,sql_text:string,saved_query_id?:number,signal?:AbortSignal)=>scoped<SqlExecution>(ws,'/execute',json({sql_text,saved_query_id},'POST',signal))
export const saveSql=(ws:string,body:{name:string; description:string; sql_text:string; expected_version?:number},id?:number,signal?:AbortSignal)=>scoped<SavedSql>(ws,id?`/queries/${id}`:'/queries',json(body,id?'PATCH':'POST',signal))
export const deleteSql=(ws:string,id:number,version:number,signal?:AbortSignal)=>sqlRequest<{deleted:boolean}>(base(ws)+`/queries/${id}`,json({confirmed:true,expected_version:version},'DELETE',signal))
export interface SqlSelection {column?:string;aggregation?:string;alias?:string}
export interface SqlBuilderInput {dataset:string;selections?:SqlSelection[];columns:string[];aggregation?:string;measure?:string;grouping:string[];filters:{column:string;operator:string;value?:string;end?:string}[];sort_by?:string;descending:boolean;limit:number;relationship_id?:number}
export const buildSql=(ws:string,body:SqlBuilderInput,signal?:AbortSignal)=>scoped<{workspace_id:number;status:string;sql_text?:string;diagnostics:SqlDiagnostic[]}>(ws,'/builder',json(body,'POST',signal))
