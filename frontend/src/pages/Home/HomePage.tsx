import { useCallback } from "react"
import { Link } from "react-router-dom"
import { listWorkspaces } from "../../services/api/workspaces"
import { useApiResource } from "../../services/api/useApiResource"
import { ApiFeedback } from "../../components/ui/ApiFeedback"
import { WorkspaceCard } from "../../features/workspaces/components/WorkspaceCard"
export function HomePage() {
  const load = useCallback((signal: AbortSignal) => listWorkspaces(signal), [])
  const resource = useApiResource("home-workspaces", load)
  return <div className="space-y-6">
    <header className="space-y-3"><h1 className="text-3xl font-semibold">Welcome to DataRise</h1><p className="max-w-2xl text-slate-600">Turn incoming data into trusted processing results. Start with a workspace, then create a dataset for each business entity.</p><Link className="inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-white" to="/app/workspaces">View Workspaces</Link></header>
    {!resource.data ? <ApiFeedback error={resource.error} retry={resource.retry} loading="Loading your workspaces…" /> : resource.data.length ? <section className="space-y-4"><h2 className="text-xl font-semibold">Your workspaces</h2><div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{resource.data.map(workspace => <WorkspaceCard key={workspace.id} workspace={workspace} />)}</div></section> : <section className="rounded-xl border border-slate-200 bg-white p-6"><h2 className="font-semibold">Your first workspace starts here</h2><p className="mt-2 text-slate-600">Create a workspace to organize related datasets and analytics from the Workspaces page.</p></section>}
  </div>
}
