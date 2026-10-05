import { readDatasetBatches } from "../../ingestion/data/storage"
import type { DatasetFile } from "../types"

export function DuplicateFileNotice({ file, workspaceId, datasetId }: { file: DatasetFile; workspaceId: string; datasetId: string }) {
  let batchInfo = "No ingestion batch is available for this file."
  try {
    const batch = readDatasetBatches(workspaceId, datasetId).find((batch) => batch.sourceFileId === file.id)
    if (batch) batchInfo = `${batch.id} · ${batch.status.replaceAll("_", " ")}`
  } catch { batchInfo = "Existing batch details are currently unavailable." }
  return (
    <section role="status" className="space-y-3 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
      <h3 className="font-semibold">This file has already been added to this dataset.</h3>
      <p>No additional file or ingestion batch will be created.</p>
      <dl className="space-y-2">
        <div><dt className="font-medium">Existing file</dt><dd className="break-all">{file.fileName} · File #{file.id}</dd></div>
        <div><dt className="font-medium">File status</dt><dd>{file.status.replaceAll("_", " ")}</dd></div>
        <div><dt className="font-medium">Ingestion batch</dt><dd className="break-all">{batchInfo}</dd></div>
      </dl>
    </section>
  )
}
