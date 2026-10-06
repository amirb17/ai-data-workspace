import { Button } from "./Button"
import { Link } from "react-router-dom"
export function ApiFeedback({ error, retry, loading = "Loading…", backTo, backLabel = "Back to Workspaces" }: { error?: string; retry: () => void; loading?: string; backTo?: string; backLabel?: string }) {
  return <div role={error ? "alert" : "status"} className="space-y-3 rounded-xl border border-slate-200 bg-white p-6">
    <p className="text-sm text-slate-700">{error ?? loading}</p>
    {error && <Button onClick={retry}>Retry</Button>}
    {error && backTo && <Link className="inline-flex min-h-11 items-center px-3 text-sm font-medium text-indigo-700" to={backTo}>{backLabel}</Link>}
  </div>
}
