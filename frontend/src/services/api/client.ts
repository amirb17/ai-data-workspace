export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/+$/, "")

export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number = 0) {
    super(message)
    this.status = status
    this.name = "ApiError"
  }
}
let identityMode: 'session' | 'development' = 'session'
let csrf: Promise<string> | undefined
export function setIdentityMode(mode: 'session' | 'development') { identityMode = mode }
export function announceAuthChange() { try {localStorage.setItem('datarise-auth-change', String(Date.now())+Math.random())} catch { /* Cross-tab notifications are optional. */ } }
export function clearAuthClient() { csrf = undefined; announceAuthChange(); window.dispatchEvent(new Event('datarise:signed-out')) }
async function csrfToken() {
  csrf ??= fetch(`${API_BASE_URL}/auth/csrf`, {credentials: 'include'}).then(async response => {
    if (!response.ok) throw new ApiError('Sign-in is temporarily unavailable.', response.status)
    const value = await response.json() as {csrf_token: string}
    return value.csrf_token
  }).catch(error => {csrf = undefined; throw error})
  return csrf
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    const token = identityMode === 'session' && !['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase()) ? await csrfToken() : undefined
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      credentials: 'include',
      headers: { "Content-Type": "application/json", ...options.headers, ...(token ? {'X-CSRF-Token': token} : {}) },
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
    if (response.status === 403) csrf = undefined
    if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('datarise:signed-out'))
    if (path.startsWith('/auth/') && data && typeof data === 'object' && 'detail' in data && typeof data.detail === 'string') throw new ApiError(data.detail, response.status)
    // Backend details may contain internal storage paths. Keep UI errors safe.
    const message = response.status === 401 || response.status === 403
      ? "Sign in again or check your workspace access."
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
