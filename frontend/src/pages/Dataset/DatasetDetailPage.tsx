import { useCallback } from "react"
import { Outlet, useLocation, useOutletContext, useParams } from "react-router-dom"
import { SectionNavigation, type SectionLink } from "../../components/layout/SectionNavigation"
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
  { label: "Analytics", to: "analytics" }, { label: "Data Profile", to: "profile" }, { label: "AI Understanding", to: "understanding" }, { label: "History", to: "history" },
]
const primary: SectionLink[] = [{label:"Overview",path:""},{label:"Files",path:"files"},{label:"Schema",path:"profile",sections:["profile","contract"]},{label:"Quality",path:"data-quality"},{label:"Settings",path:"rules"}]
const advanced: SectionLink[] = [{label:"Processing",path:"processing"},{label:"Analytics",path:"analytics"},{label:"Understanding",path:"understanding"},{label:"History",path:"history"}]

export function DatasetDetailPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const { workspace } = useOutletContext<{ workspace: WorkspaceListItem }>()
  const { pathname } = useLocation()
  const load = useCallback((signal: AbortSignal) => getDataset(workspaceId, datasetId, signal), [workspaceId, datasetId])
  const resource = useApiResource(`dataset:${workspaceId}:${datasetId}`, load)
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} loading="Loading dataset…" backTo={`/app/workspaces/${workspaceId}`} backLabel="Back to Workspace" />
  const dataset = resource.data
  const current = pathname.slice(`/app/workspaces/${workspaceId}/datasets/${datasetId}`.length).split("/").filter(Boolean)[0] ?? ""
  const section = tabs.find(tab => tab.to === current)?.label ?? "Overview"
  return <div className="min-w-0 space-y-5">
    <DatasetBreadcrumbs workspaceId={workspaceId} datasetId={datasetId} workspaceName={workspace.name} datasetName={dataset.name} section={section} />
    <header className="space-y-2">
      <div className="flex flex-wrap items-center gap-3"><h1 className="break-words text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl [overflow-wrap:anywhere]">{dataset.name}</h1><StatusBadge status={dataset.status} /></div>
      {dataset.description && <p className="max-w-3xl break-words text-sm leading-6 text-slate-600">{dataset.description}</p>}
    </header>
    <SectionNavigation label="Dataset sections" base={`/app/workspaces/${workspaceId}/datasets/${datasetId}`} current={current} links={primary} />
    {["profile","contract"].includes(current) && <SectionNavigation label="Schema sections" base={`/app/workspaces/${workspaceId}/datasets/${datasetId}`} current={current} links={[{label:"Data profile",path:"profile"},{label:"Contract and load policy",path:"contract"}]} secondary />}
    <details open={advanced.some(link => link.path === current)} className="text-sm"><summary className="w-fit cursor-pointer py-2 text-slate-600">More dataset tools</summary><SectionNavigation label="Dataset tools" base={`/app/workspaces/${workspaceId}/datasets/${datasetId}`} current={current} links={advanced} secondary /></details>
    <Outlet key={`${workspaceId}:${datasetId}`} context={dataset} />
  </div>
}
