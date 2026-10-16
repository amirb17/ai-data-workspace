import { useCallback } from "react"
import { Link } from "react-router-dom"
import { listWorkspaces } from "../../services/api/workspaces"
import { useApiResource } from "../../services/api/useApiResource"
import { ApiFeedback } from "../../components/ui/ApiFeedback"
export function ProcessingPage() {
  const load = useCallback((signal: AbortSignal) => listWorkspaces(signal), [])
  const resource = useApiResource("activity-workspaces", load)
  return <section className="space-y-5"><header><h1 className="text-2xl font-semibold">Activity</h1><p className="mt-1 text-sm text-slate-600">Choose a workspace to inspect dataset deliveries, pending decisions and processing outcomes.</p></header>{!resource.data ? <ApiFeedback loading="Loading workspaces…" error={resource.error} retry={resource.retry} /> : <div className="divide-y rounded-xl border bg-white">{resource.data.filter(workspace => workspace.status !== "ARCHIVED").map(workspace => <Link key={workspace.id} className="flex min-h-16 flex-wrap items-center justify-between gap-2 p-4" to={`/app/workspaces/${workspace.id}/processing`}><span className="break-words font-medium">{workspace.name}</span><span className="text-sm text-indigo-700">View processing</span></Link>)}{!resource.data.length && <p className="p-5 text-sm text-slate-600">No workspaces yet. <Link className="text-indigo-700 underline" to="/app/workspaces">Create a workspace</Link> to begin.</p>}</div>}</section>
}
