import { useCallback } from "react"
import { Link, useParams, useSearchParams } from "react-router-dom"
import { useProcessingContext } from "../../../features/ingestion/useProcessingContext"
import { RuleReviewForm } from "../../../features/ingestion/components/RuleReviewForm"
import { datasetProcessing } from "../../../services/api/rules"
import { useApiResource } from "../../../services/api/useApiResource"

function DeliveryRules({ workspaceId, datasetId, uploadId }: { workspaceId: string; datasetId: string; uploadId: number }) {
  const { context, error, reload } = useProcessingContext(workspaceId, datasetId, uploadId)
  if (error) return <div><p role="alert" className="text-sm text-red-700">{error}</p><button className="min-h-11 px-3" onClick={reload}>Retry status refresh</button></div>
  if (!context) return <p role="status">Loading delivery rules…</p>
  if (!context.dataset_version_file_id) return <p className="text-sm text-slate-600">Process this pending delivery in Processing to profile its source before reviewing rules.</p>
  return <RuleReviewForm key={context.dataset_version_file_id} workspaceId={workspaceId} datasetId={datasetId} associationId={context.dataset_version_file_id} onChanged={reload} />
}
export function DatasetRulesPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const [params, setParams] = useSearchParams()
  const load = useCallback((signal: AbortSignal) => datasetProcessing(workspaceId, datasetId, false, signal), [workspaceId, datasetId])
  const resource = useApiResource(`rule-deliveries:${workspaceId}:${datasetId}`, load)
  const deliveries = resource.data?.deliveries ?? []
  const requested = params.get("upload")
  const selected = requested ? deliveries.find(delivery => String(delivery.context.upload_request_id) === requested) : deliveries[0]
  return <div className="space-y-5">
    <div><h2 className="text-xl font-semibold text-slate-950">Rules</h2><p className="mt-1 text-sm text-slate-600">Review validation decisions for a delivery. Approved rules are reused when compatible.</p></div>
    {resource.error && <p role="alert" className="text-red-700">{resource.error}<button className="ml-3 min-h-11 underline" onClick={resource.retry}>Retry delivery list</button></p>}
    {resource.loading && <p role="status">Loading deliveries…</p>}
    {resource.data && (!deliveries.length ? <p className="text-sm text-slate-600">No deliveries are available. Upload a CSV in Files first.</p> : <>
      <label className="block space-y-2 text-sm"><span>Source delivery</span><select className="min-h-11 w-full max-w-full rounded-lg border border-slate-300 bg-white p-2" value={selected?.context.upload_request_id ?? ""} onChange={event => setParams({ upload: event.target.value })}>
        {!selected && <option value="">Select a delivery in this dataset</option>}
        {deliveries.map(delivery => <option key={delivery.context.upload_request_id} value={delivery.context.upload_request_id}>{delivery.source_file_name}</option>)}
      </select></label>
      {selected ? <DeliveryRules key={`${workspaceId}/${datasetId}/${selected.context.upload_request_id}`} workspaceId={workspaceId} datasetId={datasetId} uploadId={selected.context.upload_request_id} /> : <p role="alert">The selected delivery is not in this dataset.</p>}
    </>)}
    <Link className="inline-flex min-h-11 items-center text-sm font-medium text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/processing`}>Go to Processing</Link>
  </div>
}
