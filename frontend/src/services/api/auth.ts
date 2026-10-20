import {API_BASE_URL, request, clearAuthClient, setIdentityMode} from './client'
import {clearBackendScope} from '../localScope'
export type Challenge = {challenge_id: string; masked_email: string; resend_after: number; expires_in: number; message: string}
export function safeReturn(value: string | null) {
  if (!value || value.length > 2048) return '/app'
  try {
    let decoded = value
    for (let i=0;i<3;i++) decoded=decodeURIComponent(decoded)
    if (/[\\\r\n\0]/.test(decoded) || decoded.startsWith('//') || !/^\/app(?:\/|\?|#|$)/.test(decoded) || decoded.split(/[?#]/)[0].split('/').some(part=>part==='.'||part==='..')) return '/app'
    return value
  } catch {return '/app'}
}
export async function authConfig() {
  const result=await request<{google_available: boolean; identity_mode: 'session'|'development'}>('/auth/config')
  setIdentityMode(result.identity_mode)
  return result
}
export function authPost<T>(path: string, data: unknown) {return request<T>('/auth/'+path,{method:'POST',body:JSON.stringify(data)})}
export function googleUrl(returnTo: string, connect=false) {return API_BASE_URL+'/auth/google/start?return_to='+encodeURIComponent(safeReturn(returnTo))+(connect?'&connect=true':'')}
export async function signOut(){await authPost('logout',{}); clearBackendScope(); clearAuthClient()}
