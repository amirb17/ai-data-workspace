import { ApiError, backendId, request } from './client'
import type { ModelDataset } from '../../features/workspaces/model/modelView'

export async function getWorkspaceModel(workspace: string, signal?: AbortSignal) {
  const result = await request<{workspace_id:number;datasets:ModelDataset[]}>(`/workspaces/${backendId(workspace)}/model-summary`,{signal})
  if(result.workspace_id!==backendId(workspace))throw new ApiError('Model belongs to another workspace.')
  return result.datasets
}
