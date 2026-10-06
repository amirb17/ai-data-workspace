import type { ReactNode } from "react"
import { Badge } from "../../../components/ui/Badge"
import type { IngestionBatch } from "../types"
import type { ProcessingContext } from "../../../services/api/rules"
import { deliveryStatus, countLabel } from "../processingPresentation"
import { ProcessingStages } from "./ProcessingStages"
import { ProcessingDetails } from "./ProcessingDetails"

export type BatchPresentation = Omit<IngestionBatch, "rowCount" | "columnCount"> & { rowCount: number | null; columnCount: number | null }
export function IngestionBatchCard({ batch, context, children, qualityHref }: { batch: BatchPresentation; context?: ProcessingContext; children?: ReactNode; qualityHref?: string }) {
  const status = context ? deliveryStatus(context) : { label: "Ready to process", variant: "neutral" as const }
  const metrics = context?.load_strategy === "UPSERT"
    ? [["Inserted",context.inserted_rows],["Updated",context.updated_rows],["Unchanged",context.unchanged_rows],["Duplicates",context.duplicate_rows],["Conflicts",context.conflict_rows],["Older updates ignored",context.stale_rows]] as const
    : context?.load_strategy === "APPEND"
    ? [["Input",context.input_rows],["Inserted",context.inserted_rows],["Duplicates",context.duplicate_rows],["Incremental conflicts",context.incremental_rejected_rows]] as const
    : [["Input", context?.input_rows], ["Valid", context?.valid_rows], ["Quarantined", context?.rejected_rows], ["Published", context?.output_rows]] as const
  return <article className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-label={`Delivery ${batch.sourceFileName}`}>
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <h3 className="break-words font-semibold text-slate-950 [overflow-wrap:anywhere]">{batch.sourceFileName}</h3>
        <p className="mt-1 text-xs text-slate-500">Uploaded <time dateTime={batch.createdAt}>{new Date(batch.createdAt).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</time></p>
      </div>
      <Badge variant={status.variant}>{status.label}</Badge>
    </header>
    {children}
    {context && <ProcessingStages context={context} compact />}
    <dl className="grid grid-cols-2 gap-3 border-t border-slate-100 pt-3 text-sm sm:grid-cols-4">{metrics.map(([label, value]) => <div key={label}><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 font-semibold text-slate-950">{countLabel(value)}</dd></div>)}</dl>
    {context && <details className="group">
      <summary className="flex min-h-11 cursor-pointer list-item items-center rounded-lg px-2 text-sm font-medium text-indigo-700 focus-visible:outline focus-visible:outline-2">View delivery details</summary>
      <ProcessingDetails context={context} createdAt={batch.createdAt} qualityHref={qualityHref} />
    </details>}
  </article>
}
