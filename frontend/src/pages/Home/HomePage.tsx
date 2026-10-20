import { useCallback } from "react"
import { Link } from "react-router-dom"
import { listWorkspaces } from "../../services/api/workspaces"
import { useApiResource } from "../../services/api/useApiResource"
import { ApiFeedback } from "../../components/ui/ApiFeedback"
import { WorkspaceCard } from "../../features/workspaces/components/WorkspaceCard"
import {useCurrentUser} from '../../services/identityContext'
import {listDatasets} from '../../services/api/datasets'
export function HomePage() {
  const user=useCurrentUser()
  const load = useCallback(async (signal: AbortSignal) => {
    const recent=(await listWorkspaces(signal)).slice(0,6)
    return Promise.all(recent.map(async workspace=>{
      try {return {...workspace,datasetCount:(await listDatasets(String(workspace.id),signal)).length}}
      catch {return workspace}
    }))
  }, [])
  const resource = useApiResource("home-workspaces", load)
  return <div className="space-y-6">
    <header className="space-y-3"><h1 className="text-3xl font-semibold">Welcome, {user.displayName.split(' ')[0]}</h1><p className="max-w-2xl text-slate-600">Continue where you left off, or start with a new workspace.</p><Link className="inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-white" to="/app/workspaces?create=true">New workspace</Link></header>
    {!resource.data ? <ApiFeedback error={resource.error} retry={resource.retry} loading="Loading your workspaces…" /> : resource.data.length ? <section className="space-y-4"><h2 className="text-xl font-semibold">Recent workspaces</h2><div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{resource.data.map(workspace => <WorkspaceCard key={workspace.id} workspace={workspace} />)}</div></section> : <section className="rounded-xl border border-slate-200 bg-white p-6"><h2 className="font-semibold">Your first workspace starts here</h2><p className="mt-2 text-slate-600">Turn your data into trusted analytics.</p><Link className="mt-5 inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-white" to="/app/workspaces?create=true">Create your first workspace</Link><ol className="mt-6 grid gap-3 text-sm text-slate-600 sm:grid-cols-4">{['Add data','Prepare','Connect','Explore'].map((step,index)=><li key={step}>{index+1}. {step}</li>)}</ol></section>}
  </div>
}
