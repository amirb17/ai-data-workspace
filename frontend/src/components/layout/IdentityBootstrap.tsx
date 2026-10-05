import { useCallback, type ReactNode } from "react"
import { API_BASE_URL } from "../../services/api/client"
import { getCurrentUser } from "../../services/api/identity"
import { useApiResource } from "../../services/api/useApiResource"
import { IdentityContext } from "../../services/identityContext"
import { configureBackendScope } from "../../services/localScope"
import { ApiFeedback } from "../ui/ApiFeedback"
export function IdentityBootstrap({ children }: { children: ReactNode }) {
  const load = useCallback(async (signal: AbortSignal) => {
    const user = await getCurrentUser(signal)
    if (!signal.aborted) configureBackendScope(API_BASE_URL, user.userId)
    return user
  }, [])
  const resource = useApiResource("identity", load)
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} />
  return <IdentityContext.Provider value={resource.data}>{children}</IdentityContext.Provider>
}
