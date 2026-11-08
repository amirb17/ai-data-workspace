import { ApiError, backendId, request } from './client'

export type Capability = {
  workspace_id: number; dataset_id: number; csv_upload: boolean; xlsx: 'UNSUPPORTED'
  max_preview_bytes: number; processing_submission: 'DISABLED'; preview: string
  warehouse: 'NOT_CONFIGURED' | 'ACCEPTED_STATE_REQUIRED' | 'RECONCILIATION_REQUIRED'
  warehouse_models: {model_id:number;version_id:number;name:string;version_number:number}[]
}
export type Preview = {
  workspace_id:number;dataset_id:number;status:'PREVIEW'|'UNAVAILABLE';reason:string|null
  total_rows:number|null;valid_rows:number|null;rejected_rows:number|null;rule_version:number|null
  columns:string[];issues:{column:string;rule:string;count:number}[];publication_authorized:false
}
export type GoldAssessment = {workspace_id:number;dataset_id:number;status:'ASSESSED'|'UNAVAILABLE';reason:string|null;rows:number|null;columns:string[];publication_authorized:false}
export type Jobs = {workspace_id:number;dataset_id:number;jobs:{job_id:string;operation:string;status:string;attempt_count:number}[]}
const path=(w:string,d:string)=>`/workspaces/${backendId(w)}/datasets/${backendId(d)}/product`
function scope<T extends {workspace_id:number;dataset_id:number}>(value:T,w:string,d:string):T {
  if(value.workspace_id!==backendId(w)||value.dataset_id!==backendId(d))throw new ApiError('Preview scope does not match this dataset.')
  return value
}
export async function capabilities(w:string,d:string,signal?:AbortSignal){return scope(await request<Capability>(`${path(w,d)}/capabilities`,{signal}),w,d)}
export async function processingJobs(w:string,d:string,signal?:AbortSignal){return scope(await request<Jobs>(`${path(w,d)}/jobs`,{signal}),w,d)}
export async function csvBytes(file:File,maximum=2097152):Promise<string>{
  if(!file.name.toLowerCase().endsWith('.csv')||file.size<=0||file.size>Math.min(maximum,2097152))throw new ApiError('Select an original CSV up to 2 MiB. XLSX is not yet supported.')
  const bytes=new Uint8Array(await file.arrayBuffer())
  if(bytes.byteLength!==file.size)throw new ApiError('The selected file could not be read completely.')
  let binary=''
  for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768))
  return btoa(binary)
}
export async function csvPreview(w:string,d:string,upload:number,file:File,mode:'quality'|'exact',signal:AbortSignal){
  const csv_base64=await csvBytes(file)
  signal.throwIfAborted()
  const result=scope(await request<Preview>(`${path(w,d)}/uploads/${backendId(upload)}/csv-preview`,{method:'POST',signal,body:JSON.stringify({mode,csv_base64})}),w,d)
  if(result.publication_authorized!==false)throw new ApiError('Invalid preview authority response.')
  return result
}
export async function goldAssessment(w:string,d:string,upload:number,model:number,version:number,signal:AbortSignal){
  const value=scope(await request<GoldAssessment>(`${path(w,d)}/uploads/${backendId(upload)}/gold-assessment?model_id=${backendId(model)}&version_id=${backendId(version)}`,{signal}),w,d)
  if(value.publication_authorized!==false)throw new ApiError('Invalid assessment authority response.')
  return value
}
const reasons:Record<string,string>={REGISTERED_SOURCE_REQUIRED:'This delivery has no registered source version. Preview is unavailable.',RULE_APPROVAL_REQUIRED:'Approve the delivery’s rules before requesting a server preview.',EXACT_APPROVED_POLICY_REQUIRED:'This delivery has no approved exact monetary policy. Legacy numeric output is not exact money.',TRUSTED_GOLD_READER_UNAVAILABLE:'Trusted Gold reading is not enabled in this runtime.',VERIFIED_GOLD_REQUIRED:'A verified Gold result is required before assessment.',MODEL_SOURCE_VERSION_DIFFERS:'The model and Gold source versions differ. Review the model before continuing.',PREVIEW_LIMIT_EXCEEDED:'This input exceeds the bounded preview limits.',EMPTY_SOURCE:'The registered source has no data rows.',SOURCE_OR_POLICY_UNSUPPORTED:'The source or approved policy is not supported by this preview.',SOURCE_REGISTRATION_UNAVAILABLE:'The registered source is unavailable for preview.'}
export const previewReason=(reason:string|null)=>reasons[reason??'']??'This capability is currently unavailable. No processing or publication occurred.'
