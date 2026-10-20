import { backendId, request, setIdentityMode } from "./client"
export type CurrentUser = { userId: number; displayName: string; identityMode: 'development' | 'session'; email?: string; avatarUrl?: string; hasPassword?: boolean; loginMethods?: string[] }
export function mapIdentity(value: { user_id: number; display_name: string; identity_mode: 'development' | 'session'; email?: string; avatar_url?: string; has_password?: boolean; login_methods?: string[] }): CurrentUser {
  if (typeof value.display_name !== "string" || !['development','session'].includes(value.identity_mode)) throw new Error("The backend identity response is invalid.")
  setIdentityMode(value.identity_mode)
  return { userId: backendId(value.user_id), displayName: value.display_name, identityMode: value.identity_mode, ...(value.identity_mode==='session'?{email: value.email, avatarUrl: value.avatar_url, hasPassword: value.has_password, loginMethods: value.login_methods}:{}) }
}
export async function getCurrentUser(signal?: AbortSignal) {
  return mapIdentity(await request<Parameters<typeof mapIdentity>[0]>("/me", { signal }))
}
