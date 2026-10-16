import { Link, useOutletContext, useParams } from "react-router-dom"
import { ApiFeedback } from "../../../components/ui/ApiFeedback"
import { StatusBadge } from "../../../components/ui/StatusBadge"
import type { DatasetListItem } from "../../datasets/types"
import { preparationAction, useWorkspacePreparation } from "../useWorkspacePreparation"

export function WorkspacePreparation({ view }: { view: "overview" | "processing" | "quality" }) {
  const { workspaceId = "" } = useParams()
  const { datasets } = useOutletContext<{ datasets: DatasetListItem[] }>()
  const resource = useWorkspacePreparation(workspaceId, datasets)
  const titles = { overview: "Overview", processing: "Processing", quality: "Quality" }
  if (!resource.data) return <section className="space-y-4"><h2 className="text-2xl font-semibold">{titles[view]}</h2><ApiFeedback loading="Checking workspace preparation…" error={resource.error} retry={resource.retry} /></section>
  const { rows, analytics } = resource.data
  const withData = rows.filter(row => row.processing.summary.total > 0).length
  const prepared = rows.filter(row => row.analytics.current_state_id !== null).length
  const checked = rows.filter(row => row.processing.deliveries.some(delivery => delivery.context.valid_rows !== null)).length
  const fresh = rows.filter(row => row.analytics.analytics_ready).length
  const next = rows.find(row => !row.analytics.analytics_ready || !row.profile?.profile_ready || row.understanding?.status !== "READY")
  const action = !rows.length ? { label: "Add data", to: `/app/workspaces/${workspaceId}/datasets?create=1` } : next ? preparationAction(workspaceId, next) : { label: "Explore analytics", to: `/app/workspaces/${workspaceId}/analytics` }
  return <section className="min-w-0 space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-2xl font-semibold">{titles[view]}</h2><p className="mt-1 text-sm text-slate-600">{view === "overview" ? "Your workspace progress and next action." : view === "processing" ? "Deliveries run within each dataset. Open a dataset to continue or retry." : "Actual validation outcomes by dataset. Checks run does not mean a person reviewed every issue."}</p></div><button className="min-h-11 px-3 text-sm text-indigo-700" onClick={resource.retry}>Reload status</button></header>
    {view === "overview" && <><ol aria-label="Workspace progress" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{[
      ["Workspace created", "Complete"], ["Data added", `${withData} of ${rows.length} datasets have deliveries`], ["Trusted data prepared", `${prepared} of ${rows.length} datasets`], ["Quality checks run", `${checked} of ${rows.length} datasets`], ["Data connected", `${analytics.summary?.confirmed_relationships ?? "Unavailable"} current confirmed relationships`], ["Analytics available", `${fresh} current dataset sources · ${analytics.metrics_fresh} current metrics`],
    ].map(([label, value]) => <li key={label} className="rounded-xl border border-slate-200 bg-white p-4"><p className="text-sm font-medium">{label}</p><p className="mt-1 text-sm text-slate-500">{value}</p></li>)}</ol><div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-white p-5"><div><h3 className="font-semibold">Next step</h3><p className="mt-1 text-sm text-slate-600">{next ? `${next.dataset.name}: ${action.label.toLowerCase()} to continue preparation.` : !rows.length ? "Create a dataset for each logical business table." : "Your dataset analytics are current. Explore validated metrics or ask a question."}</p></div><Link className="inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-sm text-white" to={action.to}>{action.label}</Link></div></>}
    {!rows.length && view !== "overview" && <p className="rounded-xl border bg-white p-5 text-sm text-slate-600">No datasets yet. <Link className="text-indigo-700 underline" to={action.to}>Add data</Link> to begin.</p>}
    <div className="divide-y divide-slate-200 rounded-xl border bg-white">{rows.map(row => {
      const target = view === "processing" ? { label: "Open processing", to: `/app/workspaces/${workspaceId}/datasets/${row.dataset.id}/processing` } : view === "quality" ? { label: "Review quality", to: `/app/workspaces/${workspaceId}/datasets/${row.dataset.id}/data-quality` } : preparationAction(workspaceId, row)
      const latestCheck = row.processing.deliveries.filter(delivery => delivery.context.valid_rows !== null).sort((a, b) => b.context.upload_request_id - a.context.upload_request_id)[0]?.context
      return <article key={row.dataset.id} className="flex flex-wrap items-center justify-between gap-3 p-4"><div className="min-w-0 space-y-1"><Link className="break-words font-medium text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${row.dataset.id}`}>{row.dataset.name}</Link><p className="text-sm text-slate-500">{view === "quality" ? latestCheck ? `Latest checked delivery: ${latestCheck.valid_rows?.toLocaleString()} passed Silver · ${latestCheck.rejected_rows?.toLocaleString() ?? "Unavailable"} rejected${latestCheck.incremental_rejected_rows != null ? ` · ${latestCheck.incremental_rejected_rows.toLocaleString()} rejected at dataset update` : ""}` : "Quality checks have not run yet" : view === "processing" ? `${row.processing.summary.total} deliveries · ${row.processing.summary.pending} pending · ${row.processing.summary.awaiting_rules} need review · ${row.processing.summary.processing} running` : `${row.analytics.trusted_rows === null ? "Trusted row count unavailable" : `${row.analytics.trusted_rows.toLocaleString()} trusted rows`} · ${row.processing.summary.total} deliveries`}</p></div><div className="flex flex-wrap items-center gap-3"><StatusBadge status={row.analytics.freshness} /><Link className="inline-flex min-h-11 items-center text-sm font-medium text-indigo-700" to={target.to}>{target.label}</Link></div></article>
    })}</div>
  </section>
}
