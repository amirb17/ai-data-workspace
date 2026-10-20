import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { useLocation, Navigate } from 'react-router-dom'
import { API_BASE_URL } from "../../services/api/client"
import { getCurrentUser } from "../../services/api/identity"
import { useApiResource } from "../../services/api/useApiResource"
import { IdentityContext } from "../../services/identityContext"
import { configureBackendScope, clearBackendScope } from "../../services/localScope"
import { ApiFeedback } from "../ui/ApiFeedback"
export function IdentityBootstrap({ children }: { children: ReactNode }) {
  const location = useLocation()
  const expectedUser = useRef<number | undefined>(undefined)
  const [signedOut, setSignedOut] = useState(false)
  useEffect(() => {
    const expired = () => {clearBackendScope(); setSignedOut(true)}
    const check = () => { getCurrentUser().then(user => {if(expectedUser.current && user.userId !== expectedUser.current) expired()}).catch(() => { /* 401 is handled by the shared client; a network outage does not revoke a session. */ }) }
    const changed = (event: StorageEvent) => {if(event.key==='datarise-auth-change') expired()}
    window.addEventListener('datarise:signed-out', expired)
    window.addEventListener('pageshow', check)
    window.addEventListener('focus', check)
    window.addEventListener('storage', changed)
    const timer = window.setInterval(check, 60000)
    return () => {window.removeEventListener('datarise:signed-out', expired); window.removeEventListener('pageshow', check); window.removeEventListener('focus', check); window.removeEventListener('storage', changed); window.clearInterval(timer)}
  }, [])
  const load = useCallback(async (signal: AbortSignal) => {
    const user = await getCurrentUser(signal)
    if (!signal.aborted) {expectedUser.current = user.userId; configureBackendScope(API_BASE_URL, user.userId)}
    return user
  }, [])
  const resource = useApiResource("identity", load)
  if (signedOut) return <Navigate to={'/login?return_to='+encodeURIComponent(location.pathname+location.search)} replace/>
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} />
  return <IdentityContext.Provider value={resource.data}>{children}</IdentityContext.Provider>
}
