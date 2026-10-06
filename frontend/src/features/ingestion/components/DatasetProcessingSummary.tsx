import type { DatasetProcessing } from "../../../services/api/rules"

export function DatasetProcessingSummary({ data, busy, process }: { data: DatasetProcessing; busy: boolean; process: () => void }) {
  return <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4">
    <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
      {Object.entries(data.summary).map(([name, count]) => <div key={name}><dt className="capitalize text-slate-500">{name.replaceAll("_", " ")}</dt><dd className="font-semibold">{count.toLocaleString()}</dd></div>)}
    </dl>
    <button className="min-h-10 rounded-lg bg-indigo-600 px-4 text-sm text-white disabled:opacity-50" disabled={busy || data.summary.pending === 0} onClick={process}>{busy ? "Processing pending batches…" : "Process Pending Batches"}</button>
    <p className="text-sm text-slate-500">Deliveries process independently. Rule reviews and failed deliveries require your attention.</p>
    {data.operation && <p role="status" className="text-sm">{data.operation.processed} deliveries processed · {data.operation.successful} successful · {data.operation.needs_attention} needs attention</p>}
  </section>
}
