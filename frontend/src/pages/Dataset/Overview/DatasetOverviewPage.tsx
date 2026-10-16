import { Link, useParams } from "react-router-dom"
import { useDatasetSummary } from "../../../features/datasets/useDatasetSummary"
import { datasetGuidance } from "../../../features/datasets/datasetGuidance"
import { getDatasetContract } from "../../../features/datasets/contracts/storage"
import { ApiFeedback } from "../../../components/ui/ApiFeedback"
import { StatusBadge } from "../../../components/ui/StatusBadge"
import { IncrementalPolicy } from "../../../features/datasets/contracts/components/IncrementalPolicy"
import { AnalyticsReadiness } from "../../../features/datasets/AnalyticsReadiness"
import { deliveryStatus, overviewAttention, terminalDelivery, countLabel } from "../../../features/ingestion/processingPresentation"
export function DatasetOverviewPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const resource = useDatasetSummary()
  let configured = false, contractError = ""
  try { configured = !!getDatasetContract(workspaceId, datasetId) } catch { contractError = "Contract settings could not be read in this browser." }
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} loading="Loading dataset overview…" />
  const data = resource.data, latest = data.deliveries.at(-1), next = datasetGuidance(data, configured)
  const lastProcessed = data.deliveries.slice().reverse().find(d => terminalDelivery(d.context))
  const base = `/app/workspaces/${workspaceId}/datasets/${datasetId}`
  return <section className="space-y-5"><h2 className="sr-only">Dataset Overview</h2>
    <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-5"><p className="text-slate-600">{contractError || next.message}</p><Link className="inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-white" to={base + "/" + (contractError ? "contract" : next.section)}>{contractError ? "Review Contract" : next.label}</Link></div>
    {data.summary.total > 0 && <dl className="flex flex-wrap gap-6">{[["Deliveries",data.summary.total],["Pending",data.summary.pending],["Rules need review",data.summary.awaiting_rules],["Needs attention",overviewAttention(data)]].map(([label,value]) => <div key={label}><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 text-lg font-semibold">{value}</dd></div>)}</dl>}
    <AnalyticsReadiness workspaceId={workspaceId} datasetId={datasetId} />
    <details className="space-y-3 rounded-xl border border-slate-200 bg-white p-4"><summary className="min-h-11 cursor-pointer py-2 text-sm font-medium">Inspection contract and load policy</summary><StatusBadge status={contractError ? "UNAVAILABLE" : configured ? "CONFIGURED" : "NO_CONTRACT"} /><p className="text-sm text-slate-600">Inspection settings are saved in this browser. The backend load policy below controls trusted dataset updates.</p><IncrementalPolicy workspaceId={workspaceId} datasetId={datasetId} summaryOnly /><Link className="inline-flex min-h-11 items-center text-sm text-indigo-700" to={`${base}/contract`}>Review contract and policy</Link></details>
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5"><h3 className="font-semibold">Latest delivery</h3>{latest ? <><p className="break-words font-medium">{latest.source_file_name}</p><p>{deliveryStatus(latest.context).label}</p><p className="text-sm text-slate-600">{latest.context.rule_state === "FINALIZED" ? `Approved Rule Version ${latest.context.rule_version}${latest.context.rules_reused ? " reused" : ""}` : latest.context.status === "AWAITING_RULES" ? "Rules need review" : "Rules not yet approved"}</p></> : <p className="text-sm text-slate-600">No deliveries yet. Add a CSV to begin.</p>}</section>
    <details className="space-y-3 rounded-xl border border-slate-200 bg-white p-4"><summary className="min-h-11 cursor-pointer py-2 text-sm font-medium">Last completed processing</summary>{lastProcessed ? <><p className="break-words font-medium">{lastProcessed.source_file_name}</p><p>{deliveryStatus(lastProcessed.context).label}</p><p className="text-sm text-slate-600">{lastProcessed.context.load_strategy === "UPSERT" ? `${countLabel(lastProcessed.context.inserted_rows)} inserted · ${countLabel(lastProcessed.context.updated_rows)} updated · ${countLabel(lastProcessed.context.unchanged_rows)} unchanged · ${countLabel(lastProcessed.context.rejected_rows)} quarantined · ${countLabel(lastProcessed.context.incremental_rejected_rows)} rejected at dataset update` : lastProcessed.context.load_strategy === "APPEND" ? `${countLabel(lastProcessed.context.inserted_rows)} inserted · ${countLabel(lastProcessed.context.duplicate_rows)} duplicates · ${countLabel(lastProcessed.context.incremental_rejected_rows)} incremental conflicts` : `${countLabel(lastProcessed.context.valid_rows)} valid · ${countLabel(lastProcessed.context.rejected_rows)} quarantined · ${countLabel(lastProcessed.context.output_rows)} published`}</p></> : <p className="text-sm text-slate-600">No completed processing yet. Follow your deliveries in Processing.</p>}</details>
  </section>
}
