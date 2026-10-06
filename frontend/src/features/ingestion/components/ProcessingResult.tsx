import { Link } from "react-router-dom"
import type { ProcessingContext } from "../../../services/api/rules"
import { countLabel, rateLabel, terminalDelivery, deliveryStatus, issueLabel } from "../processingPresentation"

export function ProcessingResult({ context, qualityHref }: { context: ProcessingContext; qualityHref?: string }) {
  const metrics = [["Input rows", countLabel(context.input_rows)], ["Valid rows", countLabel(context.valid_rows)],
    ["Quarantined rows", countLabel(context.rejected_rows)], ["Gold output rows", countLabel(context.output_rows)],
    ["Acceptance rate", rateLabel(context.valid_rows, context.input_rows)], ["Quarantine rate", rateLabel(context.rejected_rows, context.input_rows)],
    ["Duplicates", countLabel(context.duplicate_rows)], ["Updated rows", countLabel(context.updated_rows)]]
  return <section className="space-y-4" aria-label="Delivery result">
    <h4 className="font-semibold text-slate-950">{terminalDelivery(context) ? "Processing Result" : "Row outcomes"}</h4>
    <dl className="grid grid-cols-2 gap-4 lg:grid-cols-4">{metrics.map(([label, value]) => <div key={label}><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 font-semibold text-slate-950">{value}</dd></div>)}</dl>
    {terminalDelivery(context) && <p className="font-medium">{deliveryStatus(context).label}</p>}
    {context.stages.gold === "SKIPPED" && <p className="text-amber-800">No valid rows were available for analytics output.</p>}
    {(context.rejected_rows ?? 0) > 0 && <aside className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-950" aria-label="Data quality attention">
      <p className="font-semibold">{countLabel(context.rejected_rows)} {context.rejected_rows === 1 ? "row needs" : "rows need"} attention</p>
      <p>{countLabel(context.rejected_rows)} of {countLabel(context.input_rows)} rows were quarantined during validation.</p>
      {context.issue_summary.length > 0 && <p className="text-xs">{context.issue_summary.map(issue => `${issueLabel(issue.rule_type)}: ${countLabel(issue.violation_count)}`).join(" · ")}</p>}
      {qualityHref && <Link className="inline-flex min-h-11 items-center rounded-lg px-2 font-medium underline focus-visible:outline focus-visible:outline-2" to={qualityHref}>View Data Quality</Link>}
    </aside>}
  </section>
}
