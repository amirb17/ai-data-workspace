import { ApiError, backendId, request } from "./client"
export async function archiveDelivery(workspaceId: string, datasetId: string, uploadId: number) {
  const value = await request<{ upload_request_id: number; workspace_id: number; dataset_id: number; archived_at: string }>(`/files/uploads/${backendId(uploadId)}/archive`, {
    method: "POST", body: JSON.stringify({workspace_id:backendId(workspaceId),dataset_id:backendId(datasetId)}),
  }).catch(error => {
    if (error instanceof ApiError && error.status === 409) throw new ApiError("This delivery is currently processing and cannot be archived yet. Refresh and retry when processing has stopped.",409)
    throw error
  })
  if (value.workspace_id !== backendId(workspaceId) || value.dataset_id !== backendId(datasetId) || value.upload_request_id !== uploadId || !value.archived_at) throw new ApiError("Delivery archive response mismatch. Refresh the list.")
  return value
}
