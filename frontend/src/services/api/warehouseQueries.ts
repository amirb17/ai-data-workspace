import {ApiError, backendId, request} from './client'

export type WarehouseQueryState = 'ADMITTED'|'EXPIRED'|'PREPARED'|'RUNNING'|'SUCCEEDED'|'FAILED'|'CANCELLED'|'UNCERTAIN'
export interface WarehouseValidation {valid:true;model_id:number;model_version_id:number;columns:string[];row_limit:number;estimated_bytes:number;budget_decision:'ALLOWED'|'REVIEW_REQUIRED'|'BLOCKED';reason:string;query_execution_enabled:false}
export interface WarehouseAdmission {attempt_handle:string;reused:boolean;query_execution_enabled:false}
export interface WarehouseStatus {attempt_handle:string;status:WarehouseQueryState;results_available:boolean;query_execution_enabled:false}
export type WarehouseCell = string|number|boolean|null
export interface WarehouseResults {attempt_handle:string;columns:string[];rows:WarehouseCell[][];has_more:boolean;bytes_encoded:number;query_execution_enabled:false}
const states = new Set(['ADMITTED','EXPIRED','PREPARED','RUNNING','SUCCEEDED','FAILED','CANCELLED','UNCERTAIN'])
const record=(v:unknown):Record<string,unknown>=>{if(!v||typeof v!=='object'||Array.isArray(v))throw new ApiError('Invalid warehouse response.');return v as Record<string,unknown>}
const integer=(v:unknown,min=0):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=min
const columns=(v:unknown):v is string[]=>Array.isArray(v)&&v.length>0&&v.length<=32&&v.every(c=>typeof c==='string'&&/^[A-Za-z_][A-Za-z_0-9]{0,127}$/.test(c))&&new Set(v).size===v.length
export function queryHandle(handle:string){if(!/^[a-f0-9]{32}$/.test(handle))throw new ApiError('Enter the original 32-character query handle.');return handle}
const base=(workspace:string)=>`/workspaces/${backendId(workspace)}/warehouse/queries`
function scoped(v:unknown,handle?:string){const r=record(v);if(typeof r.attempt_handle!=='string'||(handle&&r.attempt_handle!==handle)||r.query_execution_enabled!==false)throw new ApiError('Unexpected warehouse query identity or execution policy.');queryHandle(r.attempt_handle);return r}
async function call(path:string,options:RequestInit){try{return await request<unknown>(path,options)}catch(error){if(error instanceof ApiError){const message=error.status===400||error.status===422?'Warehouse validation failed. Check the approved model and SQL, or inspect the backend validation-stage log.':error.status===409?'The query cannot run with the current warehouse publication or budget. Check the model publishing state or refresh the original attempt.':error.status===503?'Warehouse runtime is unavailable or disabled. Keep the original attempt for a safe retry.':error.message;throw new ApiError(message,error.status)}throw error}}
const post=(body?:unknown,signal?:AbortSignal):RequestInit=>({method:'POST',signal,...(body?{body:JSON.stringify(body)}:{})})
export async function validateWarehouseQuery(workspace:string,model:number,sql:string,signal?:AbortSignal):Promise<WarehouseValidation>{
 const r=record(await call(base(workspace)+'/validate',post({model_id:backendId(model),sql},signal)))
 if(r.valid!==true||r.model_id!==model||!integer(r.model_version_id,1)||!columns(r.columns)||!integer(r.row_limit,1)||r.row_limit>500||!integer(r.estimated_bytes)||!['ALLOWED','REVIEW_REQUIRED','BLOCKED'].includes(String(r.budget_decision))||typeof r.reason!=='string'||r.reason.length>256||r.query_execution_enabled!==false)throw new ApiError('Unexpected warehouse validation response.')
 return {valid:true,model_id:model,model_version_id:r.model_version_id,columns:r.columns,row_limit:r.row_limit,estimated_bytes:r.estimated_bytes,budget_decision:r.budget_decision as WarehouseValidation['budget_decision'],reason:r.reason,query_execution_enabled:false}
}
export async function admitWarehouseQuery(workspace:string,model:number,sql:string,key:string,signal?:AbortSignal):Promise<WarehouseAdmission>{
 const r=scoped(await call(base(workspace),post({model_id:backendId(model),sql,idempotency_key:key},signal)))
 if(typeof r.reused!=='boolean')throw new ApiError('Invalid warehouse admission response.')
 return {attempt_handle:r.attempt_handle as string,reused:r.reused,query_execution_enabled:false}
}
export async function warehouseQueryStatus(workspace:string,handle:string,operation?:'execute'|'refresh'|'cancel',signal?:AbortSignal):Promise<WarehouseStatus>{
 const r=scoped(await call(base(workspace)+'/'+queryHandle(handle)+(operation?'/'+operation:''),operation?post(undefined,signal):{signal}),handle)
 if(!states.has(String(r.status))||typeof r.results_available!=='boolean'||r.results_available!==(r.status==='SUCCEEDED'))throw new ApiError('Invalid warehouse status response.')
 return {attempt_handle:handle,status:r.status as WarehouseQueryState,results_available:r.results_available,query_execution_enabled:false}
}
export async function warehouseQueryResults(workspace:string,handle:string,signal?:AbortSignal):Promise<WarehouseResults>{
 const r=scoped(await call(base(workspace)+'/'+queryHandle(handle)+'/results',{signal}),handle)
 if(!columns(r.columns)||!Array.isArray(r.rows)||r.rows.length>100||!integer(r.bytes_encoded)||r.bytes_encoded>256*1024||typeof r.has_more!=='boolean')throw new ApiError('Invalid warehouse results response.')
 const names=r.columns
 if(!r.rows.every(row=>Array.isArray(row)&&row.length===names.length&&row.every(c=>c===null||typeof c==='boolean'||(typeof c==='string'&&c.length<=8192)||(typeof c==='number'&&Number.isFinite(c)&&(!Number.isInteger(c)||Number.isSafeInteger(c))))))throw new ApiError('Invalid warehouse result values.')
 return {attempt_handle:handle,columns:names,rows:r.rows,has_more:r.has_more,bytes_encoded:r.bytes_encoded,query_execution_enabled:false}
}
