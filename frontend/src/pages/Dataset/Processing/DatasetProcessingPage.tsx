import { useEffect, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { IngestionBatchCard } from "../../../features/ingestion/components/IngestionBatchCard"
import { DeliveryRuleBridge } from "../../../features/ingestion/components/DeliveryRuleBridge"
import { batchStorageKey, readDatasetBatches } from "../../../features/ingestion/data/storage"
import type { IngestionBatch } from "../../../features/ingestion/types"

export function DatasetProcessingPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const [, refresh] = useState(0)
  useEffect(() => {
    const update = () => refresh((value) => value + 1)
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === batchStorageKey(workspaceId, datasetId)) update()
    }
    window.addEventListener("storage", onStorage)
    window.addEventListener("datarise-batches-changed", update)
    return () => { window.removeEventListener("storage", onStorage); window.removeEventListener("datarise-batches-changed", update) }
  }, [workspaceId, datasetId])
  let batches: IngestionBatch[] = []
  let error = ""
  try { if (workspaceId && datasetId) batches = readDatasetBatches(workspaceId, datasetId) }
  catch (caught) { error = caught instanceof Error ? caught.message : "Unable to read ingestion batches." }
  return (
    <div className="space-y-5">
      <div><h2 className="text-xl font-semibold text-slate-950">Processing</h2><p className="mt-1 text-sm text-slate-500">Prepare Bronze profiles, then review backend business rules. Silver and Gold execution are not available in this phase.</p></div>
      {error ? <p role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-700">{error}</p>
        : batches.length ? <div className="space-y-4">{batches.map((batch) => batch.uploadRequestId ? <DeliveryRuleBridge key={batch.id} batch={batch} workspaceId={workspaceId} datasetId={datasetId} /> : <IngestionBatchCard key={batch.id} batch={batch} />)}</div>
        : <section className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center sm:p-12">
          <h3 className="font-semibold text-slate-950">No ingestion batches yet</h3>
          <p className="mt-2 text-sm text-slate-500">Accept a CSV in Files to create a batch ready for future processing.</p>
          <Link className="mt-4 inline-flex min-h-10 items-center rounded-lg px-3 text-sm font-medium text-indigo-600 focus-visible:outline focus-visible:outline-2" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/files`}>Go to Files</Link>
        </section>}
    </div>
  )
}
