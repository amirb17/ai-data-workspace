export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/+$/, "")

export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number = 0) {
    super(message)
    this.status = status
    this.name = "ApiError"
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    })
  } catch (error) {
    if (options.signal?.aborted) throw error
    throw new ApiError("Backend unavailable. Check the API connection and retry.")
  }
  let data: unknown
  try { data = await response.json() } catch {
    throw new ApiError(`The backend returned an invalid response (HTTP ${response.status}).`, response.status)
  }
  if (!response.ok) {
    // Backend details may contain internal storage paths. Keep UI errors safe.
    const message = response.status === 401 || response.status === 403
      ? "Access denied. Check the development identity and workspace ownership."
      : response.status === 404 ? "The requested workspace, dataset, or upload was not found."
      : response.status === 400 ? "The backend rejected the upload context or metadata. Check workspace access, dataset membership and CSV file details."
      : response.status === 422 ? "The backend rejected the request fields. Check the selected identities and file details."
      : `The backend request failed (HTTP ${response.status}). Please retry.`
    throw new ApiError(message, response.status)
  }
  return data as T
}

export function backendId(value: string | number): number {
  const id = Number(value)
  if (!Number.isSafeInteger(id) || id <= 0) throw new ApiError("Invalid backend identity.")
  return id
}
