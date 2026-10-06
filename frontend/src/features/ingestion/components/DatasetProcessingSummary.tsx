import type { DatasetProcessing } from "../../../services/api/rules"
import { datasetReadyMessage, overviewWarnings, overviewAttention } from "../processingPresentation"

export function DatasetProcessingSummary({ data, busy, process }: { data: DatasetProcessing; busy: boolean; process: () => void }) {
  const metrics = [["Deliveries", data.summary.total], ["Pending", data.summary.pending], ["Completed", data.summary.successful],
    ["With warnings", overviewWarnings(data)], ["Rules need review", data.summary.awaiting_rules], ["Needs attention", overviewAttention(data)]] as const
  return <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-labelledby="processing-overview">
    <h3 id="processing-overview" className="font-semibold text-slate-950">Processing Overview</h3>
    <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">{metrics.map(([label, count]) => <div key={label}><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 text-xl font-semibold text-slate-950">{count.toLocaleString()}</dd></div>)}</dl>
    {data.summary.failed > 0 && <p className="text-sm text-red-800">{data.summary.failed} failed {data.summary.failed === 1 ? "delivery requires" : "deliveries require"} an explicit retry.</p>}
    {data.summary.processing > 0 && <p className="text-sm text-indigo-800">{data.summary.processing} {data.summary.processing === 1 ? "delivery is" : "deliveries are"} currently processing.</p>}
    {(data.summary.pending > 0 || busy) ? <button className="min-h-11 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white disabled:opacity-50" disabled={busy} onClick={process}>{busy ? "Processing pending deliveries…" : `Process Pending Deliveries (${data.summary.pending})`}</button>
      : data.deliveries.length > 0 && <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700">{datasetReadyMessage(data)}</p>}
    {!busy && data.operation && <p role="status" className="text-sm text-slate-700">Processing finished: {data.operation.processed} deliveries handled · {data.operation.successful} completed · {data.operation.needs_attention} need attention. Completed deliveries may include quality warnings.</p>}
  </section>
}
