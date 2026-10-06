import { useCallback } from "react"
import { NavLink, Outlet, useLocation, useOutletContext, useParams } from "react-router-dom"
import { StatusBadge } from "../../components/ui/StatusBadge"
import { getDataset } from "../../services/api/datasets"
import { useApiResource } from "../../services/api/useApiResource"
import { ApiFeedback } from "../../components/ui/ApiFeedback"
import { DatasetBreadcrumbs } from "../../features/datasets/components/DatasetBreadcrumbs"
import type { WorkspaceListItem } from "../../features/workspaces/types"

const tabs = [
  { label: "Overview", to: "", end: true }, { label: "Files", to: "files" },
  { label: "Contract", to: "contract" }, { label: "Rules", to: "rules" },
  { label: "Processing", to: "processing" }, { label: "Data Quality", to: "data-quality" },
  { label: "Analytics", to: "analytics" }, { label: "History", to: "history" },
]

export function DatasetDetailPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const { workspace } = useOutletContext<{ workspace: WorkspaceListItem }>()
  const { pathname } = useLocation()
  const load = useCallback((signal: AbortSignal) => getDataset(workspaceId, datasetId, signal), [workspaceId, datasetId])
  const resource = useApiResource(`dataset:${workspaceId}:${datasetId}`, load)
  if (!resource.data) return <ApiFeedback error={resource.error ? "Dataset not found or unavailable. It may not belong to this workspace." : undefined} retry={resource.retry} loading="Loading dataset…" backTo={`/app/workspaces/${workspaceId}`} backLabel="Back to Workspace" />
  const dataset = resource.data
  const current = pathname.slice(`/app/workspaces/${workspaceId}/datasets/${datasetId}`.length).split("/").filter(Boolean)[0] ?? ""
  const section = tabs.find(tab => tab.to === current)?.label ?? "Overview"
  return <div className="min-w-0 space-y-5">
    <DatasetBreadcrumbs workspaceId={workspaceId} datasetId={datasetId} workspaceName={workspace.name} datasetName={dataset.name} section={section} />
    <header className="space-y-2">
      <div className="flex flex-wrap items-center gap-3"><h1 className="break-words text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl [overflow-wrap:anywhere]">{dataset.name}</h1><StatusBadge status={dataset.status} /></div>
      {dataset.description && <p className="max-w-3xl break-words text-sm leading-6 text-slate-600">{dataset.description}</p>}
    </header>
    <nav aria-label="Dataset sections" className="flex flex-wrap gap-x-4 gap-y-1 border-b border-slate-200">
      {tabs.map(tab => <NavLink key={tab.label} to={tab.to} end={tab.end} className={({ isActive }) => `inline-flex min-h-11 items-center border-b-2 px-1 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 ${isActive ? "border-indigo-600 text-indigo-600" : "border-transparent text-slate-500 hover:text-slate-900"}`}>{tab.label}</NavLink>)}
    </nav>
    <Outlet key={`${workspaceId}:${datasetId}`} context={dataset} />
  </div>
}
