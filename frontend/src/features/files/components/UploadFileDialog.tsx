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
import { acceptDatasetFile } from "../data/storage"
import { DatasetCompatibility } from "./DatasetCompatibility"
import { inspectCsv } from "../utils/inspectCsv"
import { DatasetContractForm } from "../../datasets/contracts/components/DatasetContractForm"
import { createInitialContract } from "../../datasets/contracts/validation"
import { contractStorageKey, saveDatasetContract, getDatasetContract } from "../../datasets/contracts/storage"
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
  onAccepted: () => void
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
  onAccepted,
  onClose,
}: UploadFileDialogProps) {
  const [configuring, setConfiguring] = useState(false)
  const acceptanceIdRef = useRef("")
  const acceptedRef = useRef(false)
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

  useEffect(() => {
    if (!inspection) return
    const update = () => {
      try {
        setSchemaMatch(compareSchema(inspection.columns, getDatasetContract(workspaceId, datasetId)))
        setError("")
      } catch (caught) {
        setSchemaMatch(null)
        setError(caught instanceof Error ? caught.message : "Unable to read the contract.")
      }
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === contractStorageKey(workspaceId, datasetId)) update()
    }
    window.addEventListener("storage", onStorage)
    window.addEventListener("datarise-contract-changed", update)
    return () => { window.removeEventListener("storage", onStorage); window.removeEventListener("datarise-contract-changed", update) }
  }, [inspection, workspaceId, datasetId])

  if (!open) {
    return null
  }

  function reset() {
    requestRef.current += 1
    setInspection(null)
    setConfiguring(false)
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
  function acceptInspection() {
    if (!inspection || !schemaMatch || acceptedRef.current ||
        schemaMatch.status === "BREAKING" || schemaMatch.status === "WRONG_DATASET_LIKELY") return
    try {
      const existingContract = getDatasetContract(workspaceId, datasetId)
      const currentMatch = compareSchema(inspection.columns, existingContract)
      if (currentMatch.status === "BREAKING" || currentMatch.status === "WRONG_DATASET_LIKELY") {
        setSchemaMatch(currentMatch)
        setError("The dataset contract changed. This file cannot be accepted.")
        return
      }
      if (!existingContract) {
        setSchemaMatch(currentMatch)
        setConfiguring(true)
        return
      }
      acceptDatasetFile(workspaceId, datasetId, inspection, acceptanceIdRef.current)
      acceptedRef.current = true
      onAccepted()
      handleClose()
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "Unable to save this file in browser storage.")
    }
  }

  function saveInitialContract(contract: DatasetContract) {
    if (!inspection) return
    if (getDatasetContract(workspaceId, datasetId)) {
      setConfiguring(false)
      setSchemaMatch(compareSchema(inspection.columns, getDatasetContract(workspaceId, datasetId)))
      setError("A contract now exists. Review the comparison before accepting this file.")
      return
    }
    saveDatasetContract(workspaceId, datasetId, contract)
    setSchemaMatch(compareSchema(inspection.columns, contract))
    setConfiguring(false)
    setError("")
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
      acceptanceIdRef.current = crypto.randomUUID()
      acceptedRef.current = false
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
          {configuring && inspection && (
            <section aria-labelledby="contract-setup-title" className="space-y-4">
              <h3 id="contract-setup-title" tabIndex={-1} ref={(element) => element?.focus()} className="text-lg font-semibold text-slate-950">Configure Dataset Contract</h3>
              <DatasetContractForm initialContract={createInitialContract(workspaceId, datasetId, datasetName, inspection.columns)}
                onSave={saveInitialContract} onCancel={() => setConfiguring(false)} />
            </section>
          )}
          {!inspection && !configuring && (
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

          {inspection && !configuring && (
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
              {schemaMatch && <DatasetCompatibility schemaMatch={schemaMatch} />}

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

        {!configuring && <div className="flex flex-col-reverse gap-3 border-t border-slate-200 px-5 py-4 sm:flex-row sm:justify-end">
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
        if (schemaMatch.status === "NO_CONTRACT") setConfiguring(true)
        else acceptInspection()
      }}
    >
      {schemaMatch.status ===
        "NO_CONTRACT"
        ? "Configure Dataset Contract"
        : "Accept File"}
    </Button>
  )}
        </div>}
      </div>
    </div>
  )
}