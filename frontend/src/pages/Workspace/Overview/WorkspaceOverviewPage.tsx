import { Link, useOutletContext, useParams } from "react-router-dom"
import { DatasetList } from "../../../features/datasets/components/DatasetList"
import type { DatasetListItem } from "../../../features/datasets/types"
export function WorkspaceOverviewPage() {
  const { workspaceId = "" } = useParams()
  const { datasets } = useOutletContext<{ datasets: DatasetListItem[] }>()
  return <section className="space-y-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-xl font-semibold">Workspace Overview</h2><p className="text-sm text-slate-600">Open a dataset to review deliveries, rules, and processing outcomes.</p></div><Link className="inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-white" to={`/app/workspaces/${workspaceId}/datasets?create=1`}>Add Dataset</Link></div><DatasetList datasets={datasets} workspaceId={workspaceId} /></section>
}
