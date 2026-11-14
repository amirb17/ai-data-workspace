import { ApiError, backendId, request } from './client'
import type { ProcessingContext } from './rules'
export type Stage = 'BRONZE' | 'SILVER' | 'GOLD'
export type StageJob = {job_id:string;upload_id:number;operation:Stage;status:string;attempt_count:number}
export const activeJob = (job:StageJob) => ['QUEUED','RUNNING','RETRY_WAIT'].includes(job.status)
export function nextStage(context:ProcessingContext):Stage|null {
  if (context.archived_at) return null
  if (['READY_TO_PROCESS','BRONZE_FAILED'].includes(context.status)) return 'BRONZE'
  if (context.rule_state !== 'FINALIZED' || !context.can_continue) return null
  if (['READY_FOR_SILVER','SILVER_FAILED','READY_TO_APPLY','DATASET_UPDATE_FAILED'].includes(context.status)) return 'SILVER'
  if (['READY_FOR_GOLD','GOLD_FAILED'].includes(context.status)) return 'GOLD'
  return null
}
export async function readStageJobs(w:string,d:string,signal?:AbortSignal):Promise<StageJob[]> {
  const timeout = AbortSignal.timeout(15000)
  const value = await request<{workspace_id:number;dataset_id:number;jobs:StageJob[]}>(
    '/workspaces/'+backendId(w)+'/datasets/'+backendId(d)+'/product/jobs',{signal:signal?AbortSignal.any([signal,timeout]):timeout})
  if (value.workspace_id!==backendId(w)||value.dataset_id!==backendId(d)) throw new ApiError('Job scope mismatch.')
  return value.jobs
}
export async function submitStage(w:string,d:string,context:ProcessingContext,jobs:StageJob[]) {
  if (context.workspace_id!==backendId(w)||context.dataset_id!==backendId(d)) throw new ApiError('Processing scope mismatch.')
  if (jobs.some(job=>job.upload_id===context.upload_request_id&&activeJob(job))) return
  const operation = nextStage(context)
  if (!operation) throw new ApiError('This delivery is not ready. Refresh its processing status.')
  const prior = jobs.find(job=>job.upload_id===context.upload_request_id&&job.operation===operation)
  // Deterministic across repeated clicks, request timeouts and reloads. Only an
  // explicit retry of a terminal failure uses a new admission key.
  const retry = prior && ['FAILED','CANCELLED'].includes(prior.status) ? prior.job_id : 'initial'
  const idempotency_key = ['ui1',w,d,context.upload_request_id,operation,context.rule_version??0,context.load_policy?.policy_id??0,retry].join(':')
  return request<{job_id:string}>('/files/uploads/'+backendId(context.upload_request_id)+'/jobs',{
    method:'POST',signal:AbortSignal.timeout(30000),body:JSON.stringify({operation,idempotency_key})})
}
