import {
  FileSpreadsheet,
  Upload,
} from "lucide-react"
import {
  useState,
} from "react"
import { useOutletContext, useParams } from "react-router-dom"
import { UploadFileDialog } from "../../../features/files/components/UploadFileDialog"
import { Button } from "../../../components/ui/Button"
import { FileStatusBadge } from "../../../features/files/components/FileStatusBadge"
import { readDatasetFiles } from "../../../features/files/data/storage"

import type { DatasetListItem } from "../../../features/datasets/types"

function formatUploadedAt(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function formatFileSize(
  bytes: number,
) {
  if (bytes < 1024) {
    return `${bytes} B`
  }

  if (bytes < 1024 * 1024) {
    return `${(
      bytes / 1024
    ).toFixed(1)} KB`
  }

  return `${(
    bytes /
    (1024 * 1024)
  ).toFixed(1)} MB`
}

export function DatasetFilesPage() {
  const {
    workspaceId,
    datasetId,
  } = useParams()
  const dataset = useOutletContext<DatasetListItem>()
  const [uploadOpen, setUploadOpen] =
  useState(false)
  const [, refreshFiles] = useState(0)
  let files: ReturnType<typeof readDatasetFiles> = []
  let storageError = ""
  try {
    if (workspaceId && datasetId) files = readDatasetFiles(workspaceId, datasetId)
  } catch (caughtError) {
    storageError = caughtError instanceof Error ? caughtError.message : "Unable to read browser file storage."
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-950">
            Files
          </h2>

          <p className="mt-1 text-sm text-slate-500">
            Upload and manage source files for this dataset.
          </p>
        </div>

        <Button
          onClick={() =>
            setUploadOpen(true)
          }
        >
          <Upload size={17} />
          Upload File
        </Button>
      </section>

      {storageError && <p role="alert" className="text-sm text-red-700">{storageError}</p>}
      {files.length === 0 ? (
        <section className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100 text-slate-500">
            <FileSpreadsheet
              size={22}
            />
          </div>

          <h3 className="mt-4 text-base font-semibold text-slate-950">
            No files yet
          </h3>

          <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-slate-500">
            Choose a CSV file to inspect against this dataset’s contract.
          </p>

          <Button
          onClick={() =>
            setUploadOpen(true)
          }
        >
          <Upload size={17} />
          Upload File
        </Button>
        </section>
      ) : (
        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs font-medium uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-5 py-3">
                    File
                  </th>

                  <th className="px-5 py-3">
                    Status
                  </th>

                  <th className="px-5 py-3">
                    Rows
                  </th>

                  <th className="px-5 py-3">
                    Columns
                  </th>

                  <th className="px-5 py-3">
                    Size
                  </th>

                  <th className="px-5 py-3">
                    Uploaded
                  </th>
                </tr>
              </thead>

              <tbody className="divide-y divide-slate-100">
                {files.map(
                  (file) => (
                    <tr
                      key={file.id}
                      className="transition hover:bg-slate-50"
                    >
                      <td className="px-5 py-4">
                        <div className="flex items-center gap-3">
                          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-500">
                            <FileSpreadsheet
                              size={17}
                            />
                          </div>

                          <div>
                            <p className="font-medium text-slate-900">
                              {
                                file.fileName
                              }
                            </p>

                            <p className="mt-0.5 text-xs text-slate-500">
                              {
                                file.fileType
                              }
                            </p>
                          </div>
                        </div>
                      </td>

                      <td className="px-5 py-4">
                        <FileStatusBadge
                          status={
                            file.status
                          }
                        />
                      </td>

                      <td className="px-5 py-4 text-slate-600">
                        {file.rowCount?.toLocaleString() ??
                          "—"}
                      </td>

                      <td className="px-5 py-4 text-slate-600">
                        {file.columnCount ??
                          "—"}
                      </td>

                      <td className="px-5 py-4 text-slate-600">
                        {formatFileSize(
                          file.sizeBytes,
                        )}
                      </td>

                      <td className="px-5 py-4 text-slate-500">
                        {
                          formatUploadedAt(file.uploadedAt)
                        }
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>

          <div className="divide-y divide-slate-100 md:hidden">
            {files.map((file) => (
              <div
                key={file.id}
                className="p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500">
                      <FileSpreadsheet
                        size={17}
                      />
                    </div>

                    <div className="min-w-0">
                      <p className="truncate font-medium text-slate-900">
                        {
                          file.fileName
                        }
                      </p>

                      <p className="mt-1 text-xs text-slate-500">
                        {file.fileType} ·{" "}
                        {formatFileSize(
                          file.sizeBytes,
                        )}
                      </p>
                    </div>
                  </div>

                  <FileStatusBadge
                    status={
                      file.status
                    }
                  />
                </div>

                <div className="mt-4 grid grid-cols-3 gap-3 text-sm">
                  <div>
                    <p className="text-xs text-slate-500">
                      Rows
                    </p>

                    <p className="mt-1 font-medium text-slate-900">
                      {file.rowCount?.toLocaleString() ??
                        "—"}
                    </p>
                  </div>

                  <div>
                    <p className="text-xs text-slate-500">
                      Columns
                    </p>

                    <p className="mt-1 font-medium text-slate-900">
                      {file.columnCount ??
                        "—"}
                    </p>
                  </div>

                  <div>
                    <p className="text-xs text-slate-500">
                      Uploaded
                    </p>

                    <p className="mt-1 font-medium text-slate-900">
                      {
                        formatUploadedAt(file.uploadedAt)
                      }
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {uploadOpen && workspaceId && datasetId && (
  <UploadFileDialog
    open={uploadOpen}
    workspaceId={workspaceId}
    datasetName={dataset.name}
    datasetId={datasetId}
    onAccepted={() => refreshFiles((version) => version + 1)}
    onClose={() =>
      setUploadOpen(false)
    }
  />
)}
    </div>
  )
}