import { Link, useParams } from "react-router-dom"
import { DeliveryRuleBridge } from "../../../features/ingestion/components/DeliveryRuleBridge"
import { DatasetProcessingSummary } from "../../../features/ingestion/components/DatasetProcessingSummary"
import { useDatasetProcessing } from "../../../features/ingestion/useDatasetProcessing"
import { readDatasetBatches } from "../../../features/ingestion/data/storage"
import { ProcessingActivity } from "../../../features/ingestion/components/ProcessingActivity"

export function DatasetProcessingPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const { data, error, busy, requestActive, onRequestActivity, process, reload } = useDatasetProcessing(workspaceId, datasetId)
  // Inspection metadata can enrich rows before Bronze. Lifecycle comes from PostgreSQL.
  const local = (() => { try { return readDatasetBatches(workspaceId, datasetId) } catch { return [] } })()
  return <div className="space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-xl font-semibold text-slate-950">Processing</h2><p className="mt-1 text-sm text-slate-500">Follow each delivery from uploaded source to trusted analytics output.</p></div><button className="min-h-11 rounded-lg border border-slate-200 px-3 text-sm" onClick={reload}>Refresh delivery status</button></div>
    {error && <p role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-700">{error} Displayed results may be out of date.<button className="ml-3 min-h-11 underline" onClick={reload}>Retry status refresh</button></p>}
    {!data && !error && <p role="status">Loading delivery status and results…</p>}
    {data && <DatasetProcessingSummary data={data} busy={busy} process={() => void process()} />}
    {data && <ProcessingActivity data={data} busy={requestActive} />}
    {!!data?.deliveries.length && <h3 className="font-semibold text-slate-950">Recent deliveries</h3>}
    {data?.deliveries.slice().reverse().map(delivery => {
      const context = delivery.context
      const inspection = local.find(b => b.uploadRequestId === context.upload_request_id && b.sourceFileId === context.file_id)
      const batch = { id: `upload-${context.upload_request_id}`, workspaceId: context.workspace_id, datasetId: context.dataset_id,
        uploadRequestId: context.upload_request_id, sourceFileId: context.file_id, sourceFileName: delivery.source_file_name,
        createdAt: delivery.created_at, status: "READY_TO_PROCESS" as const, rowCount: context.input_rows ?? inspection?.rowCount ?? null,
        columnCount: context.column_count ?? inspection?.columnCount ?? null, validRows: null, rejectedRows: null, duplicateRows: null, updatedRows: null }
      return <DeliveryRuleBridge key={`${workspaceId}:${datasetId}:${context.upload_request_id}`} batch={batch} context={context} workspaceId={workspaceId} datasetId={datasetId} reload={reload} datasetBusy={busy} onRequestActivity={onRequestActivity} />
    })}
    {data && !data.deliveries.length && <section className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center sm:p-12">
      <h3 className="font-semibold text-slate-950">No deliveries yet</h3><p className="mt-2 text-sm text-slate-500">Upload a CSV to start building this dataset.</p>
      <Link className="mt-4 inline-flex min-h-10 items-center rounded-lg px-3 text-sm font-medium text-indigo-600" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/files`}>Go to Files</Link>
    </section>}
  </div>
}
