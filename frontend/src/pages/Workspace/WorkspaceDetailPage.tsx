import { useCallback } from "react"
import { Link, NavLink, Outlet, useMatch, useParams } from "react-router-dom"
import { getWorkspace } from "../../services/api/workspaces"
import { listDatasets } from "../../services/api/datasets"
import { useApiResource } from "../../services/api/useApiResource"
import { ApiFeedback } from "../../components/ui/ApiFeedback"
import { StatusBadge } from "../../components/ui/StatusBadge"
const tabs = [{label:"Overview",to:"",end:true},{label:"Datasets",to:"datasets"},{label:"Understanding",to:"understanding"},{label:"Processing",to:"processing"},{label:"Data Quality",to:"data-quality"},{label:"Analytics",to:"analytics"}]
export function WorkspaceDetailPage() {
  const { workspaceId = "" } = useParams()
  const datasetRoute = useMatch("/app/workspaces/:workspaceId/datasets/:datasetId/*")
  const load = useCallback(async (signal: AbortSignal) => {
    const [workspace, datasets] = await Promise.all([getWorkspace(workspaceId, signal), listDatasets(workspaceId, signal)])
    return { workspace, datasets }
  }, [workspaceId])
  const resource = useApiResource(`workspace:${workspaceId}`, load)
  if (!resource.data) return <ApiFeedback error={resource.error ? "Could not open this workspace. It may not exist or you may not have access." : undefined} retry={resource.retry} loading="Loading workspace…" backTo="/app/workspaces" />
  const { workspace, datasets } = resource.data
  const context = { workspace, datasets, onDatasetsChanged: resource.retry }
  if (datasetRoute) return <Outlet key={workspaceId} context={context} />
  return <div className="min-w-0 space-y-5">
    <nav aria-label="Breadcrumb"><Link className="inline-flex min-h-11 items-center text-sm text-indigo-700" to="/app/workspaces">Workspaces</Link><span aria-hidden="true"> › </span><span aria-current="page" className="break-words text-sm">{workspace.name}</span></nav>
    <header className="space-y-2"><div className="flex flex-wrap items-center gap-3"><h1 className="break-words text-3xl font-semibold [overflow-wrap:anywhere]">{workspace.name}</h1><StatusBadge status={workspace.status} /></div><p className="break-words text-sm text-slate-600">{workspace.description}</p><p className="text-sm text-slate-500">{datasets.length} {datasets.length === 1 ? "dataset" : "datasets"}</p></header>
    <nav aria-label="Workspace sections" className="flex flex-wrap gap-x-4 border-b border-slate-200">{tabs.map(tab => <NavLink key={tab.to} to={tab.to} end={tab.end} className={({isActive}) => `inline-flex min-h-11 items-center border-b-2 px-1 text-sm font-medium ${isActive ? "border-indigo-600 text-indigo-700" : "border-transparent text-slate-600"}`}>{tab.label}</NavLink>)}</nav>
    <Outlet key={workspaceId} context={context} />
  </div>
}
