import { Link } from "react-router-dom"
import type { ProcessingContext } from "../../../services/api/rules"
import { countLabel, rateLabel, terminalDelivery, deliveryStatus, issueLabel } from "../processingPresentation"

export function ProcessingResult({ context, qualityHref }: { context: ProcessingContext; qualityHref?: string }) {
  const identityRejected = ["UPSERT","SNAPSHOT"].includes(context.load_strategy ?? "") ? (context.incremental_rejected_rows ?? 0) - (context.conflict_rows ?? 0) : (context.incremental_rejected_rows ?? 0)
  const metrics = [["Input rows", countLabel(context.input_rows)], ["Valid rows", countLabel(context.valid_rows)],
    ["Quarantined rows", countLabel(context.rejected_rows)], ["Gold output rows", countLabel(context.output_rows)],
    ["Acceptance rate", rateLabel(context.valid_rows, context.input_rows)], ["Quarantine rate", rateLabel(context.rejected_rows, context.input_rows)],
    ["Duplicates", countLabel(context.duplicate_rows)], ["Updated rows", countLabel(context.updated_rows)],
    ...(["APPEND","UPSERT","SNAPSHOT"].includes(context.load_strategy ?? "") ? [["Inserted",countLabel(context.inserted_rows)],[context.load_strategy === "APPEND" ? "Incremental conflicts" : "Rejected during dataset update",countLabel(context.incremental_rejected_rows)],["Current Dataset",countLabel(context.current_state_rows)]] : []),
    ...(["UPSERT","SNAPSHOT"].includes(context.load_strategy ?? "") ? [["Unchanged",countLabel(context.unchanged_rows)],["Conflicts",countLabel(context.conflict_rows)],["Older updates ignored",countLabel(context.stale_rows)]] : []),
    ...(context.load_strategy === "SNAPSHOT" ? [["Deactivated",countLabel(context.deactivated_rows)],["Reactivated",countLabel(context.reactivated_rows)],["Active records",countLabel(context.active_rows)],["Inactive records",countLabel(context.inactive_rows)]] : [])]
  return <section className="space-y-4" aria-label="Delivery result">
    <h4 className="font-semibold text-slate-950">{terminalDelivery(context) ? "Processing Result" : "Row outcomes"}</h4>
    {context.load_strategy && <p className="text-sm">Load strategy: {context.load_strategy}{context.load_strategy === "UPSERT" && context.load_policy ? ` by ${context.load_policy.business_keys.join(" + ")}` : ""}</p>}
    {["APPEND","UPSERT","SNAPSHOT"].includes(context.load_strategy ?? "") && <p className="text-sm text-slate-600">Current Dataset records the trusted row count when this delivery was applied. Overview shows the latest dataset state. Gold output rows describe this delivery and may include records excluded by the dataset update.</p>}
    {context.load_strategy === "SNAPSHOT" && <div className="space-y-2 text-sm text-slate-600"><p>Snapshot coverage: {context.snapshot_context?.coverage === "COMPLETE" ? "Complete" : "Partial / unknown"}</p>{context.snapshot_context?.coverage !== "COMPLETE" && <p>This delivery is not marked as a complete snapshot. Missing records will not be deactivated.</p>}{(context.deactivated_rows ?? 0) > 0 && <p>{countLabel(context.deactivated_rows)} records were marked inactive because they were missing from this complete snapshot.</p>}<p>Reactivated records are previously inactive records that appeared again in this snapshot.</p>{context.snapshot_outcome === "STALE" && <p className="text-amber-900">Older snapshot detected. This delivery was retained for history but did not replace newer trusted data.</p>}{context.snapshot_outcome === "DEACTIVATION_WITHHELD" && <p className="text-amber-900">Missing records were preserved. Validation issues or correction/backfill context withheld deactivation.</p>}</div>}
    <dl className="grid grid-cols-2 gap-4 lg:grid-cols-4">{metrics.map(([label, value]) => <div key={label}><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 font-semibold text-slate-950">{value}</dd></div>)}</dl>
    {terminalDelivery(context) && <p className="font-medium">{deliveryStatus(context).label}</p>}
    {context.stages.gold === "SKIPPED" && <p className="text-amber-800">No valid rows were available for analytics output.</p>}
    {identityRejected > 0 && <p className="text-sm text-amber-900">{countLabel(identityRejected)} validated rows were not applied because of invalid record identity or event time. Rejected records did not replace trusted data.</p>}
    {(context.conflict_rows ?? 0) > 0 && <p className="text-sm text-amber-900">{countLabel(context.conflict_rows)} records contained conflicting values for the same business key and were not applied.</p>}
    {(context.stale_rows ?? 0) > 0 && <p className="text-sm text-amber-900">{countLabel(context.stale_rows)} {context.stale_rows === 1 ? "older update was" : "older updates were"} ignored because newer data already exists.</p>}
    {(context.rejected_rows ?? 0) > 0 && <aside className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-950" aria-label="Data quality attention">
      <p className="font-semibold">{countLabel(context.rejected_rows)} {context.rejected_rows === 1 ? "row needs" : "rows need"} attention</p>
      <p>{countLabel(context.rejected_rows)} of {countLabel(context.input_rows)} rows were quarantined during validation.</p>
      {context.issue_summary.length > 0 && <p className="text-xs">{context.issue_summary.map(issue => `${issueLabel(issue.rule_type)}: ${countLabel(issue.violation_count)}`).join(" · ")}</p>}
      {qualityHref && <Link className="inline-flex min-h-11 items-center rounded-lg px-2 font-medium underline focus-visible:outline focus-visible:outline-2" to={qualityHref}>View Data Quality</Link>}
    </aside>}
  </section>
}
