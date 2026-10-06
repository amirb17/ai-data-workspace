import { Link } from "react-router-dom"
import { StatusBadge } from "../../../components/ui/StatusBadge"
import type { DatasetListItem } from "../types"
import { DatasetReadiness } from "./DatasetReadiness"
export function DatasetList({ datasets, workspaceId, filtered = false }: { datasets: DatasetListItem[]; workspaceId: string; filtered?: boolean }) {
  if (!datasets.length) return <section className="rounded-xl border border-slate-300 border-dashed bg-white p-6 text-center"><h2 className="font-semibold">{filtered ? "No datasets match your filters" : "No datasets yet"}</h2><p className="mt-2 text-sm text-slate-600">{filtered ? "Change the search or status filter to see your datasets." : "Use Add Dataset to create a logical business table, such as Customers or Orders."}</p></section>
  return <div className="grid gap-4 md:grid-cols-2">{datasets.map(dataset => <article key={dataset.id} className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><h3 className="break-words font-semibold [overflow-wrap:anywhere]">{dataset.name}</h3><StatusBadge status={dataset.status} /></div>
    {dataset.description && <p className="break-words text-sm text-slate-600">{dataset.description}</p>}
    <DatasetReadiness workspaceId={workspaceId} datasetId={dataset.id} />
    {dataset.updatedAt !== "Unavailable" && <p className="text-xs text-slate-500">Updated {new Date(dataset.updatedAt).toLocaleString()}</p>}
    <Link className="inline-flex min-h-11 items-center font-medium text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${dataset.id}`}>Open Dataset<span className="sr-only">: {dataset.name}</span></Link>
  </article>)}</div>
}
