import { useCallback } from "react"
import { Link, Outlet, useLocation, useMatch, useParams } from "react-router-dom"
import { getWorkspace } from "../../services/api/workspaces"
import { listDatasets } from "../../services/api/datasets"
import { useApiResource } from "../../services/api/useApiResource"
import { ApiFeedback } from "../../components/ui/ApiFeedback"
import { SectionNavigation, type SectionLink } from "../../components/layout/SectionNavigation"
const tabs: SectionLink[] = [{label:"Overview",path:""},{label:"Data",path:"datasets",sections:["datasets","processing"]},{label:"Quality",path:"data-quality"},{label:"Model",path:"data-model",sections:["data-model","relationships","understanding","metrics"]},{label:"Analytics",path:"analytics"},{label:"SQL",path:"sql"}]
const dataTabs: SectionLink[] = [{label:"Datasets",path:"datasets"},{label:"Processing",path:"processing"}]
const modelTabs: SectionLink[] = [{label:"Data model",path:"data-model"},{label:"Review relationships",path:"relationships"},{label:"Dataset descriptions",path:"understanding"},{label:"Metric definitions",path:"metrics"}]
export function WorkspaceDetailPage() {
  const { workspaceId = "" } = useParams()
  const datasetRoute = useMatch("/app/workspaces/:workspaceId/datasets/:datasetId/*")
  const { pathname } = useLocation()
  const base = `/app/workspaces/${workspaceId}`
  const current = pathname.slice(base.length).split("/").filter(Boolean)[0] ?? ""
  const section = tabs.find(tab => (tab.sections ?? [tab.path]).includes(current))?.label ?? "Overview"
  const load = useCallback(async (signal: AbortSignal) => {
    const [workspace, datasets] = await Promise.all([getWorkspace(workspaceId, signal), listDatasets(workspaceId, signal)])
    return { workspace, datasets }
  }, [workspaceId])
  const resource = useApiResource(`workspace:${workspaceId}`, load)
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} loading="Loading workspace…" backTo="/app/workspaces" />
  const { workspace, datasets } = resource.data
  const context = { workspace, datasets, onDatasetsChanged: resource.retry }
  if (datasetRoute) return <Outlet key={workspaceId} context={context} />
  return <div className="min-w-0 space-y-5">
    <nav aria-label="Breadcrumb" className="text-sm text-slate-500"><Link className="inline-flex min-h-11 items-center text-indigo-700" to="/app/workspaces">Workspaces</Link><span aria-hidden="true"> / </span><Link className="inline-flex min-h-11 items-center break-words text-indigo-700" to={base}>{workspace.name}</Link><span aria-hidden="true"> / </span><span aria-current="page">{section}</span></nav>
    <SectionNavigation label="Workspace sections" base={base} current={current} links={tabs} />
    {section === "Data" && <SectionNavigation label="Data sections" base={base} current={current} links={dataTabs} secondary />}
    {section === "Model" && <SectionNavigation label="Model sections" base={base} current={current} links={modelTabs} secondary />}
    <Outlet key={workspaceId} context={context} />
  </div>
}
