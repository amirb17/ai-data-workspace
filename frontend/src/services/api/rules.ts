import { ApiError, backendId, request } from "./client"
import type { DeliveryApplication, SnapshotContext, SnapshotCoverage } from "./incremental"
export type ProcessingContext = {
  upload_request_id: number; workspace_id: number; dataset_id: number; file_id: number
  archived_at?: string | null; archived_by?: number | null
  status: string; dataset_version_file_id: number | null; dataset_version_id: number | null
  dataset_version_number: number | null; rule_version: number | null
  rule_state: "DRAFT" | "FINALIZED" | null; active_rule_count: number; silver_can_proceed: boolean
  rules_reused: boolean; current_rule_version: number | null
  can_continue: boolean
  load_strategy?: string; application?: DeliveryApplication | null
  inserted_rows?: number | null; incremental_rejected_rows?: number | null; current_state_rows?: number | null
  unchanged_rows?: number | null; conflict_rows?: number | null; stale_rows?: number | null
  deactivated_rows?: number | null; reactivated_rows?: number | null; active_rows?: number | null; inactive_rows?: number | null
  snapshot_context?: SnapshotContext | null; snapshot_outcome?: string | null
  load_policy?: { policy_id: number; policy_version: number; business_keys: string[]; event_time_column: string | null; snapshot_coverage?: SnapshotCoverage | null }
  state_lineage?: { source_state_version: number | null; result_state_version: number } | null
  stages: { bronze: string; rules: string; silver: string; gold: string; dataset_update?: string }
  latest_attempt: { id: number; stage: string; status: string; started_at: string | null; completed_at: string | null } | null
  started_at: string | null; completed_at: string | null; error_summary: string | null
  gold_skip_reason: string | null; column_count: number | null
  input_rows: number | null; valid_rows: number | null; rejected_rows: number | null; output_rows: number | null
  duplicate_rows: number | null; updated_rows: number | null; quarantine_available: boolean
  issue_summary: { rule_type: string; violation_count: number }[]
}
export type RuleAnswer = { column_name: string; rule_type: string; answer: string }
export type DatasetProcessing = {
  workspace_id: number; dataset_id: number
  deliveries: { source_file_name: string; created_at: string; size_bytes?: number | null; context: ProcessingContext }[]
  summary: { total: number; pending: number; processing: number; awaiting_rules: number; successful: number; failed: number; needs_attention: number }
  operation?: { processed: number; successful: number; needs_attention: number }
}
export async function datasetProcessing(w: string, d: string, process = false, signal?: AbortSignal) {
  const value = verifyScope(await ruleRequest<DatasetProcessing>(`/workspaces/${backendId(w)}/datasets/${backendId(d)}/processing${process ? "/pending" : ""}`, { method: process ? "POST" : "GET", signal }), w, d)
  for (const delivery of value.deliveries) verifyScope(delivery.context, w, d)
  return value
}
export type RuleQuestion = { column_name: string; suggested_rule_type: string; question: string; reason?: string; options: string[] }
export type RuleReview = {
  workspace_id: number; dataset_id: number; dataset_version_file_id: number; dataset_version_id: number
  status: string; rule_state: "DRAFT" | "FINALIZED"; rule_version: number
  questions: RuleQuestion[]; answers: RuleAnswer[]; active_rule_count: number
  rules_reused?: boolean; applied_rule_version?: number | null
}
export const questionKey = (q: RuleQuestion) => JSON.stringify([q.column_name, q.suggested_rule_type])
export function reviewAnswers(review: RuleReview): Record<string, string> {
  return Object.fromEntries(review.questions.map((q) => [questionKey(q), review.answers.find((a) => a.column_name === q.column_name && a.rule_type === q.suggested_rule_type)?.answer ?? ""]))
}
export const answersComplete = (review: RuleReview, answers: Record<string, string>) => review.questions.every((q) => q.options.includes(answers[questionKey(q)]))
export const requiresRuleApproval = (review: RuleReview) => review.status === "AWAITING_RULES" && !review.rules_reused
export function canFinalize(review: RuleReview, answers: Record<string, string>, dirty: boolean, busy: boolean) {
  return requiresRuleApproval(review) && answersComplete(review, answers) && review.active_rule_count > 0 && !dirty && !busy
}
const scope = (w: string, d: string) => `?workspace_id=${backendId(w)}&dataset_id=${backendId(d)}`
function verifyScope<T extends { workspace_id: number; dataset_id: number }>(value: T, w: string, d: string): T {
  if (value.workspace_id !== backendId(w) || value.dataset_id !== backendId(d)) throw new ApiError("Processing context belongs to another workspace or dataset.")
  return value
}
async function ruleRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  try { return await request<T>(path, options) } catch (error) {
    if (error instanceof ApiError && error.status === 409) throw new ApiError("Rules or processing state changed. Reload before continuing.", 409)
    if (error instanceof ApiError && (error.status === 400 || error.status === 422)) throw new ApiError("The backend rejected the rule answer or processing context. Reload and review the available options.", error.status)
    throw error
  }
}
export async function getProcessingContext(w: string, d: string, uploadId: number, signal?: AbortSignal) {
  const value = verifyScope(await ruleRequest<ProcessingContext>(`/files/uploads/${backendId(uploadId)}/processing-context${scope(w, d)}`, { signal }), w, d)
  if (value.upload_request_id !== uploadId) throw new ApiError("Upload response mismatch.")
  return value
}
export async function startBronze(w: string, d: string, uploadId: number) {
  const value = verifyScope(await ruleRequest<ProcessingContext>(`/files/uploads/${backendId(uploadId)}/process${scope(w, d)}`, { method: "POST" }), w, d)
  if (value.upload_request_id !== uploadId) throw new ApiError("Upload response mismatch.")
  return value
}
export async function continueProcessing(w: string, d: string, uploadId: number) {
  const value = verifyScope(await ruleRequest<ProcessingContext>(`/files/uploads/${backendId(uploadId)}/continue${scope(w, d)}`, { method: "POST" }), w, d)
  if (value.upload_request_id !== uploadId) throw new ApiError("Upload response mismatch.")
  return value
}
export async function getRuleReview(w: string, d: string, id: number, signal?: AbortSignal) {
  const value = verifyScope(await ruleRequest<RuleReview>(`/files/dataset-version-files/${backendId(id)}/business-rules/suggestions${scope(w, d)}`, { signal }), w, d)
  if (value.dataset_version_file_id !== id) throw new ApiError("Rule response mismatch.")
  return value
}
export function saveRuleAnswers(w: string, d: string, review: RuleReview, answers: Record<string, string>) {
  if (!answersComplete(review, answers)) throw new ApiError("Answer each question using its available options.")
  return ruleRequest(`/files/dataset-version-files/${backendId(review.dataset_version_file_id)}/business-rules${scope(w, d)}`, {
    method: "POST", body: JSON.stringify({ expected_rule_version: review.rule_version, answers: review.questions.map((q) => ({ column_name: q.column_name, rule_type: q.suggested_rule_type, answer: answers[questionKey(q)] })) }),
  })
}
export function finalizeRuleAnswers(w: string, d: string, review: RuleReview) {
  return ruleRequest<{ finalized: boolean; message?: string }>(`/files/dataset-version-files/${backendId(review.dataset_version_file_id)}/business-rules/finalize${scope(w, d)}`, {
    method: "POST", body: JSON.stringify({ expected_rule_version: review.rule_version }),
  })
}
