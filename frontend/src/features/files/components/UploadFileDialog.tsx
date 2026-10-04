import {
  FileSpreadsheet,
  Upload,
  X,
} from "lucide-react"
import {
  useEffect,
  useRef,
  useState,
} from "react"

import { Button } from "../../../components/ui/Button"
import type { CsvInspectionResult } from "../types"
import { inspectCsv } from "../utils/inspectCsv"
import { contractStorageKey, getDatasetContract } from "../../datasets/contracts/storage"
import {
  compareSchema,
  type SchemaMatchResult,
} from "../../datasets/contracts/schemaMatch"
import type { DatasetContract } from "../../datasets/contracts/types"

type UploadFileDialogProps = {
  open: boolean
  workspaceId: string
  datasetName: string
  datasetId: string
  onClose: () => void
}

function formatFileSize(
  bytes: number,
) {
  if (bytes < 1024) {
    return `${bytes} B`
  }

  if (
    bytes <
    1024 * 1024
  ) {
    return `${(
      bytes / 1024
    ).toFixed(1)} KB`
  }

  return `${(
    bytes /
    (1024 * 1024)
  ).toFixed(1)} MB`
}

export function UploadFileDialog({
  open,
  workspaceId,
  datasetName,
  datasetId,
  onClose,
}: UploadFileDialogProps) {
  const requestRef = useRef(0)
  useEffect(() => () => { requestRef.current += 1 }, [])
  const inputRef =
    useRef<HTMLInputElement>(
      null,
    )

  const [
    inspection,
    setInspection,
  ] =
    useState<CsvInspectionResult | null>(
      null,
    )

  const [
  schemaMatch,
  setSchemaMatch,
  ] =
    useState<SchemaMatchResult | null>(
      null,
    )

  const [error, setError] =
    useState("")

  const [inspecting, setInspecting] =
    useState(false)

  if (!open) {
    return null
  }

  function reset() {
    requestRef.current += 1
    setInspection(null)
    setError("")
    setInspecting(false)
    setSchemaMatch(null)

    if (inputRef.current) {
      inputRef.current.value = ""
    }
  }

  function handleClose() {
    reset()
    onClose()
  }
  function establishInitialContract() {
  if (!inspection || schemaMatch?.status !== "NO_CONTRACT") {
    return
  }

  const contract: DatasetContract = {
    datasetId: Number(datasetId),
    datasetName,

    columns:
      inspection.columns.map(
        (column) => ({
          name: column,
          dataType: "STRING",
          required: false,
        }),
      ),

    primaryKey: [],

    loadMode: "APPEND",
  }

  try {
    if (getDatasetContract(workspaceId, datasetId)) {
      setError("A contract now exists. Choose the file again to compare it.")
      setInspection(null)
      setSchemaMatch(null)
      return
    }
    localStorage.setItem(contractStorageKey(workspaceId, datasetId), JSON.stringify(contract))
    handleClose()
  } catch (caughtError) {
    setError(caughtError instanceof Error ? caughtError.message : "Unable to save the initial contract.")
  }
}

  async function handleFile(
    file: File,
  ) {
    const request = ++requestRef.current
    setError("")
    setInspection(null)
    setSchemaMatch(null)
    setInspecting(false)

    const extension =
      file.name
        .split(".")
        .pop()
        ?.toLowerCase()

    if (extension !== "csv") {
      setError(
        "Please choose a CSV file. This inspection supports CSV only.",
      )

      return
    }

    try {
      setInspecting(true)

      const result =
        await inspectCsv(file)

      if (request !== requestRef.current) return
      setInspection(result)
      const contract =
      getDatasetContract(
        workspaceId, datasetId,
      )

    const matchResult =
      compareSchema(
        result.columns,
        contract,
      )

    setSchemaMatch(
      matchResult,
    )
    } catch (caughtError) {
      if (request !== requestRef.current) return
      setInspection(null)
      setSchemaMatch(null)
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : "Unable to inspect this file.",
      )
    } finally {
      if (request === requestRef.current) setInspecting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-file-title"
        className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white shadow-xl"
      >
        <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2
              id="upload-file-title"
              className="text-lg font-semibold text-slate-950"
            >
              Upload File
            </h2>

            <p className="mt-1 text-sm text-slate-500">
              Inspect the file before adding it to this dataset.
            </p>
          </div>

          <button
            type="button"
            onClick={handleClose}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="space-y-5 p-5">
          {!inspection && (
            <>
              <input
                ref={inputRef}
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={(event) => {
                  const file =
                    event.target
                      .files?.[0]

                  event.target.value = ""
                  if (file) {
                    void handleFile(
                      file,
                    )
                  }
                }}
              />

              <button
                type="button"
                disabled={inspecting}
                onClick={() =>
                  inputRef.current?.click()
                }
                className="flex w-full flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center transition hover:border-indigo-300 hover:bg-indigo-50/30"
              >
                <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-white text-indigo-600 shadow-sm">
                  <Upload size={22} />
                </div>

                <p className="mt-4 text-sm font-semibold text-slate-900">
                  Choose a CSV file
                </p>

                <p className="mt-1 text-xs text-slate-500">
                  Inspection only. No file upload or processing starts.
                </p>
              </button>
            </>
          )}

          {inspecting && (
            <div className="rounded-lg bg-blue-50 px-4 py-3 text-sm text-blue-700">
              Inspecting file structure...
            </div>
          )}

          {error && (
            <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {inspection && (
            <div className="space-y-5">
              <section className="rounded-xl border border-slate-200 p-4">
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-600">
                    <FileSpreadsheet
                      size={19}
                    />
                  </div>

                  <div className="min-w-0">
                    <p className="truncate font-semibold text-slate-900">
                      {
                        inspection.fileName
                      }
                    </p>

                    <p className="mt-1 text-sm text-slate-500">
                      CSV ·{" "}
                      {formatFileSize(
                        inspection.sizeBytes,
                      )}
                    </p>
                  </div>
                </div>

                <div className="mt-5 grid gap-3 sm:grid-cols-3">
                  <div className="rounded-lg bg-slate-50 p-3">
                    <p className="text-xs text-slate-500">
                      Rows
                    </p>

                    <p className="mt-1 font-semibold text-slate-900">
                      {inspection.rowCount.toLocaleString()}
                    </p>
                  </div>

                  <div className="rounded-lg bg-slate-50 p-3">
                    <p className="text-xs text-slate-500">
                      Columns
                    </p>

                    <p className="mt-1 font-semibold text-slate-900">
                      {
                        inspection.columnCount
                      }
                    </p>
                  </div>

                  <div className="rounded-lg bg-slate-50 p-3">
                    <p className="text-xs text-slate-500">
                      File type
                    </p>

                    <p className="mt-1 font-semibold text-slate-900">
                      CSV
                    </p>
                  </div>
                </div>
              </section>

              <section>
                <h3 className="text-sm font-semibold text-slate-900">
                  Detected Columns
                </h3>

                <div className="mt-3 flex flex-wrap gap-2">
                  {inspection.columns.map(
                    (column) => (
                      <span
                        key={column}
                        className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700"
                      >
                        {column}
                      </span>
                    ),
                  )}
                </div>
              </section>
              {schemaMatch && (
  <section className="rounded-xl border border-slate-200 p-4">
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">
          Dataset Compatibility
        </h3>

        <p className="mt-1 text-sm text-slate-500">
          DataRise compared the incoming columns with the trusted dataset structure.
        </p>
      </div>

      {schemaMatch.status !==
        "NO_CONTRACT" && (
        <div className="rounded-lg bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-900">
          {
            schemaMatch.matchPercentage
          }
          % match
        </div>
      )}
    </div>

    <div className="mt-4">
      {schemaMatch.status ===
        "NO_CONTRACT" && (
        <div className="rounded-lg bg-blue-50 p-4">
          <p className="text-sm font-semibold text-blue-900">
            No dataset contract yet
          </p>

          <p className="mt-1 text-sm leading-6 text-blue-700">
            This dataset does not have a trusted schema yet. The first accepted upload can be used to establish its initial structure.
          </p>
        </div>
      )}

      {schemaMatch.status ===
        "MATCH" && (
        <div className="rounded-lg bg-green-50 p-4">
          <p className="text-sm font-semibold text-green-800">
            Schema matches
          </p>

          <p className="mt-1 text-sm text-green-700">
            The incoming file matches the current dataset contract.
          </p>
        </div>
      )}

      {schemaMatch.status ===
        "WARNING" && (
        <div className="rounded-lg bg-amber-50 p-4">
          <p className="text-sm font-semibold text-amber-800">
            Compatible with warnings
          </p>

          <p className="mt-1 text-sm text-amber-700">
            The required fields are present, but additional or missing optional columns differ from the contract.
          </p>
        </div>
      )}

      {schemaMatch.status ===
        "BREAKING" && (
        <div className="rounded-lg bg-red-50 p-4">
          <p className="text-sm font-semibold text-red-800">
            Breaking schema change
          </p>

          <p className="mt-1 text-sm text-red-700">
            One or more required columns are missing. Processing must not continue until the mapping or dataset contract is reviewed.
          </p>
        </div>
      )}

      {schemaMatch.status ===
        "WRONG_DATASET_LIKELY" && (
        <div className="rounded-lg bg-red-50 p-4">
          <p className="text-sm font-semibold text-red-800">
            This may be the wrong dataset
          </p>

          <p className="mt-1 text-sm leading-6 text-red-700">
            The incoming structure is significantly different from the trusted structure of this dataset.
          </p>
        </div>
      )}
    </div>

    {schemaMatch.missingRequiredColumns.length >
      0 && (
      <div className="mt-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Missing required columns
        </p>

        <div className="mt-2 flex flex-wrap gap-2">
          {schemaMatch.missingRequiredColumns.map(
            (column) => (
              <span
                key={column}
                className="rounded-md bg-red-50 px-2.5 py-1 text-xs font-medium text-red-700"
              >
                {column}
              </span>
            ),
          )}
        </div>
      </div>
    )}

    {schemaMatch.unexpectedColumns.length >
      0 &&
      schemaMatch.status !==
        "NO_CONTRACT" && (
        <div className="mt-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Additional columns
          </p>

          <div className="mt-2 flex flex-wrap gap-2">
            {schemaMatch.unexpectedColumns.map(
              (column) => (
                <span
                  key={column}
                  className="rounded-md bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700"
                >
                  {column}
                </span>
              ),
            )}
          </div>
        </div>
      )}
  </section>
)}

              <section>
                <h3 className="text-sm font-semibold text-slate-900">
                  Data Preview
                </h3>

                <div className="mt-3 overflow-x-auto rounded-xl border border-slate-200">
                  <table className="min-w-full text-left text-sm">
                    <thead className="bg-slate-50">
                      <tr>
                        {inspection.columns.map(
                          (column) => (
                            <th
                              key={column}
                              className="whitespace-nowrap px-4 py-3 text-xs font-medium text-slate-500"
                            >
                              {column}
                            </th>
                          ),
                        )}
                      </tr>
                    </thead>

                    <tbody className="divide-y divide-slate-100">
                      {inspection.previewRows.map(
                        (
                          row,
                          rowIndex,
                        ) => (
                          <tr
                            key={
                              rowIndex
                            }
                          >
                            {inspection.columns.map(
                              (
                                column,
                              ) => (
                                <td
                                  key={
                                    column
                                  }
                                  className="whitespace-nowrap px-4 py-3 text-slate-600"
                                >
                                  {row[
                                    column
                                  ] ||
                                    "—"}
                                </td>
                              ),
                            )}
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="rounded-xl border border-indigo-100 bg-indigo-50/50 p-4">
                <p className="text-sm font-semibold text-indigo-900">
                  Inspection complete
                </p>

                <p className="mt-1 text-sm leading-6 text-indigo-700">
                  Schema comparison is complete. This local preview does not upload the file, create an ingestion batch, or start processing.
                </p>
              </section>
            </div>
          )}
        </div>

        <div className="flex flex-col-reverse gap-3 border-t border-slate-200 px-5 py-4 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="secondary"
            onClick={handleClose}
          >
            Cancel
          </Button>

          {inspection && <Button type="button" variant="secondary" onClick={reset}>Choose another CSV</Button>}
          {inspection &&
  schemaMatch && (
    <Button
      type="button"
      disabled={
        schemaMatch.status ===
          "BREAKING" ||
        schemaMatch.status ===
          "WRONG_DATASET_LIKELY"
      }
      onClick={() => {
        if (
          schemaMatch.status ===
          "NO_CONTRACT"
        ) {
          establishInitialContract()
          return
        }

        handleClose()
      }}
    >
      {schemaMatch.status ===
        "NO_CONTRACT"
        ? "Use as Initial Structure"
        : "Done"}
    </Button>
  )}
        </div>
      </div>
    </div>
  )
}