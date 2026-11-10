import { ApiError, backendId, request } from './client'

export interface WarehouseColumn {
  name: string
  type: string
  role: 'IDENTITY' | 'MEASURE' | 'ATTRIBUTE' | 'TIME'
}

export interface WarehouseModel {
  model_id: number
  model_version_id: number
  version_number: number
  name: string
  model_type: 'FACT' | 'DIMENSION'
  status: 'APPROVED'
  columns: WarehouseColumn[]
  identity_columns: string[]
  measures: string[]
  grain: string | null
  scd_type: 'SCD_TYPE_1' | 'SCD_TYPE_2' | null
  event_time_column: string | null
  sources: Array<{dataset_id: number; dataset_name: string; purpose: 'PRIMARY' | 'DEPENDENCY'}>
  resource_registered: boolean
  serving_status: 'NOT_VERIFIED'
  queryable: false
}

export interface WarehouseCatalog {
  workspace_id: number
  models: WarehouseModel[]
  relationships: []
  relationship_status: 'NOT_MODELED'
  query_execution_enabled: false
}

export async function getWarehouseCatalog(workspace: string, signal?: AbortSignal) {
  const workspaceId = backendId(workspace)
  const result = await request<WarehouseCatalog>(`/workspaces/${workspaceId}/warehouse/catalog`, {signal})
  if (result.workspace_id !== workspaceId) throw new ApiError('Warehouse catalog belongs to another workspace.')
  return result
}

export interface WarehouseRelationship {
  relationship_id: number
  workspace_id: number
  fact_model_id: number
  fact_model_version_id: number
  dimension_model_id: number
  dimension_model_version_id: number
  name: string
  key_pairs: Array<{fact_column: string; dimension_column: string}>
  cardinality: 'MANY_TO_ONE'
  temporal_policy: 'CURRENT_MEMBER' | 'EVENT_TIME_AS_OF'
  fact_event_time_column: string | null
  reviewed_at: string
}

export async function getWarehouseRelationships(workspace: string, signal?: AbortSignal) {
  const workspaceId = backendId(workspace)
  const result = await request<{workspace_id:number;relationships:WarehouseRelationship[];query_execution_enabled:false}>(
    `/workspaces/${workspaceId}/warehouse/relationships`, {signal})
  if(result.workspace_id !== workspaceId) throw new ApiError('Warehouse relationships belong to another workspace.')
  return result.relationships
}
