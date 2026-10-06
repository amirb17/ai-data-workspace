import { Link, useParams } from "react-router-dom"
import { DeliveryRuleBridge } from "../../../features/ingestion/components/DeliveryRuleBridge"
import { DatasetProcessingSummary } from "../../../features/ingestion/components/DatasetProcessingSummary"
import { useDatasetProcessing } from "../../../features/ingestion/useDatasetProcessing"
import { readDatasetBatches } from "../../../features/ingestion/data/storage"

export function DatasetProcessingPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const { data, error, busy, process, reload } = useDatasetProcessing(workspaceId, datasetId)
  // Inspection metadata can enrich rows before Bronze. Lifecycle comes from PostgreSQL.
  const local = (() => { try { return readDatasetBatches(workspaceId, datasetId) } catch { return [] } })()
  return <div className="space-y-5">
    <div><h2 className="text-xl font-semibold text-slate-950">Processing</h2><p className="mt-1 text-sm text-slate-500">Advance pending deliveries for this dataset through Bronze, approved rules, Silver and Gold.</p></div>
    {error && <p role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-700">{error}<button className="ml-3 min-h-10 underline" onClick={reload}>Reload</button></p>}
    {!data && !error && <p role="status">Loading backend deliveries…</p>}
    {data && <DatasetProcessingSummary data={data} busy={busy} process={() => void process()} />}
    {data?.deliveries.map(delivery => {
      const context = delivery.context
      const inspection = local.find(b => b.uploadRequestId === context.upload_request_id && b.sourceFileId === context.file_id)
      const batch = { id: `upload-${context.upload_request_id}`, workspaceId: context.workspace_id, datasetId: context.dataset_id,
        uploadRequestId: context.upload_request_id, sourceFileId: context.file_id, sourceFileName: delivery.source_file_name,
        createdAt: delivery.created_at, status: "READY_TO_PROCESS" as const, rowCount: context.input_rows ?? inspection?.rowCount ?? null,
        columnCount: context.column_count ?? inspection?.columnCount ?? null, validRows: null, rejectedRows: null, duplicateRows: null, updatedRows: null }
      return <DeliveryRuleBridge key={`${workspaceId}:${datasetId}:${context.upload_request_id}`} batch={batch} context={context} workspaceId={workspaceId} datasetId={datasetId} reload={reload} datasetBusy={busy} />
    })}
    {data && !data.deliveries.length && <section className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center sm:p-12">
      <h3 className="font-semibold text-slate-950">No ingestion batches yet</h3><p className="mt-2 text-sm text-slate-500">Accept a CSV in Files to create a delivery ready for processing.</p>
      <Link className="mt-4 inline-flex min-h-10 items-center rounded-lg px-3 text-sm font-medium text-indigo-600" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/files`}>Go to Files</Link>
    </section>}
  </div>
}
