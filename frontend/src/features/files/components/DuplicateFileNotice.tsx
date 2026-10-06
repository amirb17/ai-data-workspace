import type { DatasetFile } from "../types"
export function DuplicateFileNotice({ file }: { file: DatasetFile; workspaceId: string; datasetId: string }) {
  return <section role="status" className="space-y-3 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900"><h3 className="font-semibold">This file has already been added to this dataset.</h3><p>No new delivery was created.</p><p className="break-words">Existing delivery: {file.fileName}</p></section>
}
