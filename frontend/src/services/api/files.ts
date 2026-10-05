import { ApiError, backendId, request } from "./client"
export type UploadSession = { uploadRequestId: number; putUrl: string }
export type CompletedUpload = {
  uploadRequestId: number; workspaceId: number; datasetId: number; userId: number
  isDuplicate: boolean; createdAt: string
  file: { id: number; name: string; sizeBytes: number; hash: string; status: "UPLOADED" }
}
export type UploadContext = { workspaceId: string; datasetId: string; userId: number }
type CompleteResponse = {
  upload_request_id: number; session_status: string; is_duplicate: boolean
  file: { file_id: number; file_name: string; file_size: number; file_hash: string; status: string }
  upload_request: { upload_id: number; user_id: number; workspace_id: number; dataset_id: number; file_id: number; created_at: string }
}
export function mapCompletedUpload(value: CompleteResponse, context: UploadContext, sessionId: number): CompletedUpload {
  const logical = value.upload_request
  if (value.session_status !== "COMPLETED" || value.file.status !== "UPLOADED" ||
      value.upload_request_id !== sessionId || logical.upload_id !== sessionId ||
      logical.workspace_id !== backendId(context.workspaceId) || logical.dataset_id !== backendId(context.datasetId) ||
      logical.user_id !== context.userId || logical.file_id !== value.file.file_id ||
      typeof value.is_duplicate !== "boolean" || typeof value.file.file_name !== "string" || !value.file.file_name ||
      !/^[a-f0-9]{64}$/.test(value.file.file_hash) || !Number.isSafeInteger(value.file.file_size) || value.file.file_size <= 0 ||
      !Number.isFinite(Date.parse(logical.created_at))) throw new ApiError("Upload completion metadata does not match this dataset.")
  // Explicit allowlist: storage paths and presigned URLs never enter cached metadata.
  return {
    uploadRequestId: backendId(sessionId), workspaceId: logical.workspace_id, datasetId: logical.dataset_id,
    userId: logical.user_id, isDuplicate: value.is_duplicate, createdAt: logical.created_at,
    file: { id: backendId(value.file.file_id), name: value.file.file_name, sizeBytes: value.file.file_size, hash: value.file.file_hash, status: "UPLOADED" },
  }
}
export async function initiateUpload(context: UploadContext, file: File, signal: AbortSignal): Promise<UploadSession> {
  const value = await request<{ upload_request_id: number; session_status: string; workspace_id: number; dataset_id: number; user_id: number; presigned_url: string }>("/files/initiate", {
    method: "POST", signal,
    body: JSON.stringify({ workspace_id: backendId(context.workspaceId), dataset_id: backendId(context.datasetId), user_id: context.userId, file_name: file.name, file_size: file.size, content_type: "text/csv" }),
  })
  if (value.session_status !== "INITIATED" || value.workspace_id !== backendId(context.workspaceId) || value.dataset_id !== backendId(context.datasetId) || value.user_id !== context.userId || typeof value.presigned_url !== "string") throw new ApiError("Upload session ownership mismatch.")
  return { uploadRequestId: backendId(value.upload_request_id), putUrl: value.presigned_url }
}
export async function putCsv(url: string, file: File, signal: AbortSignal) {
  let response: Response
  try { response = await fetch(url, { method: "PUT", headers: { "Content-Type": "text/csv" }, body: file, signal }) }
  catch (error) { if (signal.aborted) throw error; throw new ApiError("Secure storage upload failed. Check the connection and S3 CORS configuration, then retry.") }
  if (!response.ok) throw new ApiError(`Secure storage upload failed (HTTP ${response.status}). Retry the upload.`, response.status)
}
export async function completeUpload(context: UploadContext, sessionId: number, signal: AbortSignal) {
  const value = await request<CompleteResponse>("/files/complete", { method: "POST", signal, body: JSON.stringify({ upload_request_id: sessionId }) })
  return mapCompletedUpload(value, context, sessionId)
}
