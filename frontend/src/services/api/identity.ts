import { backendId, request } from "./client"
export type CurrentUser = { userId: number; displayName: string; identityMode: "development" }
export function mapIdentity(value: { user_id: number; display_name: string; identity_mode: "development" }): CurrentUser {
  if (typeof value.display_name !== "string" || value.identity_mode !== "development") throw new Error("The backend development identity response is invalid.")
  return { userId: backendId(value.user_id), displayName: value.display_name, identityMode: value.identity_mode }
}
export async function getCurrentUser(signal?: AbortSignal) {
  return mapIdentity(await request<Parameters<typeof mapIdentity>[0]>("/me", { signal }))
}
