import type { DatasetProcessing } from "../../../services/api/rules"
import { isRunning } from "../processingState"
import { overviewAttention } from "../processingPresentation"
import { ProcessingStages } from "./ProcessingStages"

export function ProcessingActivity({ data, busy }: { data: DatasetProcessing; busy: boolean }) {
  const running = data.deliveries.filter(d => isRunning(d.context.status))
  if (!busy && !running.length) return null
  return <section className="space-y-4 rounded-xl border border-indigo-200 bg-indigo-50 p-4" aria-label="Active processing" aria-live="polite">
    <h3 className="font-semibold text-indigo-950">Processing pending deliveries</h3>
    {running.length ? running.map(delivery => <div key={delivery.context.upload_request_id} className="space-y-3">
      <p className="break-words font-medium">Processing {delivery.source_file_name}</p><ProcessingStages context={delivery.context} />
    </div>) : <p className="text-sm text-indigo-900">The backend is checking pending deliveries. Waiting for the next stage update.</p>}
    <p className="text-sm text-indigo-900">{data.summary.successful} completed overall · {data.summary.pending} eligible pending · {data.summary.processing} processing · {overviewAttention(data)} need review</p>
  </section>
}
