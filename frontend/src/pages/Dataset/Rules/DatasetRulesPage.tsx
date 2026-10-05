import { Link, useParams, useSearchParams } from "react-router-dom"
import { readDatasetBatches } from "../../../features/ingestion/data/storage"
import { useProcessingContext } from "../../../features/ingestion/useProcessingContext"
import { RuleReviewForm } from "../../../features/ingestion/components/RuleReviewForm"

function DeliveryRules({ workspaceId, datasetId, uploadId }: { workspaceId: string; datasetId: string; uploadId: number }) {
  const { context, error, reload } = useProcessingContext(workspaceId, datasetId, uploadId)
  if (error) return <div><p role="alert" className="text-sm text-red-700">{error}</p><button className="min-h-10 px-3" onClick={reload}>Retry</button></div>
  if (!context) return <p role="status">Loading processing context…</p>
  if (!context.dataset_version_file_id) return <p className="text-sm text-slate-600">Prepare Bronze for this delivery in Processing before reviewing rules.</p>
  return <RuleReviewForm key={context.dataset_version_file_id} workspaceId={workspaceId} datasetId={datasetId} associationId={context.dataset_version_file_id} onChanged={reload} />
}
export function DatasetRulesPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const [params, setParams] = useSearchParams()
  let batches: ReturnType<typeof readDatasetBatches> = []
  let error = ""
  try { batches = readDatasetBatches(workspaceId, datasetId).filter((batch) => batch.uploadRequestId) }
  catch (caught) { error = caught instanceof Error ? caught.message : "Unable to read deliveries." }
  const requested = params.get("upload")
  const selected = requested ? batches.find((batch) => String(batch.uploadRequestId) === requested) : batches[0]
  return <div className="space-y-5">
    <div><h2 className="text-xl font-semibold text-slate-950">Business Rules</h2><p className="mt-1 text-sm text-slate-600">Review backend questions for this dataset version. Your local Dataset Contract remains a separate schema and load expectation.</p></div>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {!batches.length ? <p className="text-sm text-slate-600">No completed deliveries are available. Upload a CSV in Files first.</p> : <>
      <label className="block space-y-2 text-sm"><span>Source delivery</span><select className="min-h-11 w-full max-w-full rounded-lg border border-slate-300 bg-white p-2" value={selected?.uploadRequestId ?? ""} onChange={(event) => setParams({ upload: event.target.value })}>
        {!selected && <option value="">Select a delivery in this dataset</option>}
        {batches.map((batch) => <option key={batch.id} value={batch.uploadRequestId}>Upload #{batch.uploadRequestId} · {batch.sourceFileName}</option>)}
      </select></label>
      {selected ? <DeliveryRules key={`${workspaceId}/${datasetId}/${selected.uploadRequestId}`} workspaceId={workspaceId} datasetId={datasetId} uploadId={selected.uploadRequestId!} /> : <p role="alert">The selected upload is not in this dataset.</p>}
    </>}
    <Link className="inline-flex min-h-10 items-center text-sm text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/processing`}>Go to Processing</Link>
  </div>
}
