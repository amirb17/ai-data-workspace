import { createContext, useContext } from "react"
import type { CurrentUser } from "./api/identity"
export const IdentityContext = createContext<CurrentUser | undefined>(undefined)
export function useCurrentUser() {
  const user = useContext(IdentityContext)
  if (!user) throw new Error("Backend identity has not loaded.")
  return user
}
