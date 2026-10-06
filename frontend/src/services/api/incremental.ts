import { ApiError, backendId, request } from "./client"
export type LoadStrategy = "APPEND" | "UPSERT" | "SNAPSHOT"
export type LoadPolicy = {
  policy_id: number; dataset_id: number; dataset_version_id: number; policy_version: number
  load_strategy: LoadStrategy; business_keys: string[]; schema_evolution_policy: "STRICT" | "ALLOW_ADDITIVE"
  event_time_column: string | null
}
export type ApplicationMetrics = {
  input_rows: number | null; valid_rows: number | null; rejected_rows: number | null
  inserted_rows: number | null; updated_rows: number | null; unchanged_rows: number | null
  duplicate_rows: number | null; deactivated_rows: number | null; current_state_rows: number | null
}
export type DeliveryApplication = ApplicationMetrics & {
  application_id: number; upload_request_id: number; dataset_version_id: number; policy_id: number
  applied_rule_version: number; status: "PREPARED" | "RUNNING" | "SUCCESS" | "FAILED"
  archived_at: string | null; completed_at: string | null; failure_code: string | null
  incremental_rejected_rows?: number | null; source_file_name?: string
}
export type IncrementalFoundation = {
  workspace_id: number; dataset_id: number; execution_available: boolean
  schema_versions: { dataset_version_id: number; version_number: number; columns: { name: string; data_type: string }[] }[]
  policies: LoadPolicy[]; applications: DeliveryApplication[]
  current_state: { state_id: number; row_count: number; published_at: string } | null
}
export type PolicyInput = {
  dataset_version_id: number; expected_policy_version: number; load_strategy: LoadStrategy
  business_keys: string[]; schema_evolution_policy: "STRICT" | "ALLOW_ADDITIVE"
  event_time_column: string | null; confirm_policy_change: boolean
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
