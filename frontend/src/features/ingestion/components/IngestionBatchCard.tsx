import { Badge } from "../../../components/ui/Badge"
import type { IngestionBatch } from "../types"
import type { ProcessingContext } from "../../../services/api/rules"

const variants = {
  READY_TO_PROCESS: "neutral", PROCESSING: "processing", SUCCESS: "success",
  SUCCESS_WITH_WARNINGS: "warning", FAILED: "error",
} as const

export type BatchPresentation = Omit<IngestionBatch, "rowCount" | "columnCount"> & { rowCount: number | null; columnCount: number | null }
export function IngestionBatchCard({ batch, processingStatus, context }: { batch: BatchPresentation; processingStatus?: string; context?: ProcessingContext }) {
  const metrics = processingStatus ? [
    ["Input rows", context?.input_rows], ["Valid rows", context?.valid_rows],
    ["Rejected rows", context?.rejected_rows], ["Output rows", context?.output_rows],
    ["Duplicate rows", context?.duplicate_rows], ["Updated rows", context?.updated_rows],
  ] as const : [
    ["Valid rows", batch.validRows], ["Rejected rows", batch.rejectedRows],
    ["Duplicate rows", batch.duplicateRows], ["Updated rows", batch.updatedRows],
  ] as const
  return (
    <article className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="break-all font-semibold text-slate-950">Batch {batch.uploadRequestId ?? batch.sourceFileId}</h3>
          <p className="mt-1 break-all text-sm text-slate-600">{batch.sourceFileName}</p>
          <p className="mt-1 break-all text-xs text-slate-500">Source file #{batch.sourceFileId} · {batch.id}</p>
        </div>
        <Badge variant={processingStatus === "SUCCESS_WITH_WARNINGS" ? "warning" : processingStatus ? "neutral" : variants[batch.status]}>{processingStatus === "SUCCESS_WITH_WARNINGS" ? "Completed with warnings" : (processingStatus ?? batch.status).replaceAll("_", " ")}</Badge>
      </div>
      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        <div><dt className="text-slate-500">Rows</dt><dd className="font-medium text-slate-900">{batch.rowCount?.toLocaleString() ?? "—"}</dd></div>
        <div><dt className="text-slate-500">Columns</dt><dd className="font-medium text-slate-900">{batch.columnCount?.toLocaleString() ?? "—"}</dd></div>
        <div><dt className="text-slate-500">Created</dt><dd className="text-slate-900"><time dateTime={batch.createdAt}>{new Date(batch.createdAt).toLocaleString()}</time></dd></div>
      </dl>
      {batch.batchLabel && <p className="break-words text-sm text-slate-600">Label: {batch.batchLabel}</p>}
      {batch.period && <p className="break-words text-sm text-slate-600">Period: {batch.period}</p>}
      {!processingStatus && batch.status === "READY_TO_PROCESS" && <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">Processing has not started. Row outcomes are unavailable.</p>}
      <dl className="grid grid-cols-2 gap-3 border-t border-slate-100 pt-4 text-sm sm:grid-cols-4">
        {metrics.map(([label, value]) => <div key={label}><dt className="text-slate-500">{label}</dt><dd className="mt-1 font-medium text-slate-900">{value == null ? "—" : value.toLocaleString()}</dd></div>)}
      </dl>
    </article>
  )
}
