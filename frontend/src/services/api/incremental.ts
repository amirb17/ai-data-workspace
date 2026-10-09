import { ApiError, backendId, request } from "./client"
export type LoadStrategy = "APPEND" | "UPSERT" | "SNAPSHOT"
export type SnapshotCoverage = "COMPLETE" | "PARTIAL"
export type SnapshotContext = { coverage: SnapshotCoverage; effective_at: string | null; delivery_kind: "NORMAL" | "CORRECTION" | "BACKFILL" }
export type LoadPolicy = {
  policy_id: number; dataset_id: number; dataset_version_id: number; policy_version: number
  load_strategy: LoadStrategy; business_keys: string[]; schema_evolution_policy: "STRICT" | "ALLOW_ADDITIVE"
  event_time_column: string | null
  snapshot_coverage?: SnapshotCoverage | null
}
export type ApplicationMetrics = {
  input_rows: number | null; valid_rows: number | null; rejected_rows: number | null
  inserted_rows: number | null; updated_rows: number | null; unchanged_rows: number | null
  duplicate_rows: number | null; deactivated_rows: number | null; current_state_rows: number | null
  conflict_rows?: number | null; stale_rows?: number | null
  reactivated_rows?: number | null; active_rows?: number | null; inactive_rows?: number | null
}
export type DeliveryApplication = ApplicationMetrics & {
  application_id: number; upload_request_id: number; dataset_version_id: number; policy_id: number
  applied_rule_version: number; status: "PREPARED" | "RUNNING" | "SUCCESS" | "FAILED"
  archived_at: string | null; completed_at: string | null; failure_code: string | null
  started_at?: string | null
  incremental_rejected_rows?: number | null; source_file_name?: string
  source_state_version?: number | null; result_state_version?: number | null
  result_state_id?: number | null
  snapshot_coverage?: SnapshotCoverage | null; snapshot_effective_at?: string | null
  snapshot_outcome?: string | null; snapshot_boundary_at?: string | null; delivery_kind?: string | null
}
export type IncrementalFoundation = {
  workspace_id: number; dataset_id: number; execution_available: boolean
  schema_versions: { dataset_version_id: number; version_number: number; columns: { name: string; data_type: string }[]; event_time_columns?: string[] }[]
  policies: LoadPolicy[]; applications: DeliveryApplication[]
  current_state: { state_id: number; row_count: number; published_at: string; state_analytics_status?: "STALE" | "FRESH" | "REFRESHING" | "FAILED" | null; policy_id?: number; active_rows?: number | null; inactive_rows?: number | null; snapshot_boundary_at?: string | null } | null
}
export type PolicyInput = {
  dataset_version_id: number; expected_policy_version: number; load_strategy: LoadStrategy
  business_keys: string[]; schema_evolution_policy: "STRICT" | "ALLOW_ADDITIVE"
  event_time_column: string | null; confirm_policy_change: boolean
  snapshot_coverage?: SnapshotCoverage | null
}
export async function saveSnapshotContext(workspaceId: string, datasetId: string, uploadId: number, input: SnapshotContext) {
  const value = await request<SnapshotContext & { workspace_id: number; dataset_id: number; upload_request_id: number }>(path(workspaceId,datasetId) + `/deliveries/${backendId(uploadId)}/snapshot-context`, { method: "POST", body: JSON.stringify(input) })
  if (value.workspace_id !== backendId(workspaceId) || value.dataset_id !== backendId(datasetId) || value.upload_request_id !== uploadId) throw new Error("Snapshot declaration response mismatch.")
  return value
}
function path(workspaceId: string, datasetId: string) {
  return `/workspaces/${backendId(workspaceId)}/datasets/${backendId(datasetId)}/incremental`
}
export async function getIncrementalFoundation(workspaceId: string, datasetId: string, signal?: AbortSignal) {
  const data = await request<IncrementalFoundation>(path(workspaceId, datasetId), { signal })
  if (data.workspace_id !== backendId(workspaceId) || data.dataset_id !== backendId(datasetId)) throw new Error("Incremental response belongs to another dataset.")
  return data
}
export async function saveLoadPolicy(workspaceId: string, datasetId: string, input: PolicyInput) {
  try {
    const result = await request<LoadPolicy>(path(workspaceId, datasetId) + "/policies", { method: "POST", body: JSON.stringify(input) })
    if (result.dataset_id !== backendId(datasetId) || result.dataset_version_id !== input.dataset_version_id) throw new Error("Policy response mismatch.")
    return result
  } catch (error) {
    if (error instanceof ApiError && [400,409,422].includes(error.status)) throw new Error("Policy could not be saved. Check schema, key columns and confirmation; refresh if the policy changed. A published state requires a migration before changing its policy.", { cause: error })
    throw error
  }
}
