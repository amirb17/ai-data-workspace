import type { ProcessingContext } from "../../../services/api/rules"

export function ProcessingDetails({ context }: { context: ProcessingContext }) {
  const format = (value: string) => value === "SKIPPED" ? "Skipped" : value === "PROCESSING" ? "Running" : value === "SUCCESS" ? "Complete" : value.replaceAll("_", " ")
  return <div className="space-y-3 rounded-lg bg-slate-50 p-3 text-sm">
    <dl aria-label="Processing stages" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {Object.entries(context.stages).map(([stage, state]) => <div key={stage}><dt className="capitalize text-slate-500">{stage}</dt><dd className="break-words font-medium">{format(state)}</dd></div>)}
    </dl>
    {context.rule_version != null && context.rule_version > 0 && context.rule_state === "FINALIZED" && <p>Applied rule version {context.rule_version}{context.rules_reused ? " · Approved rules reused" : ""}</p>}
    {context.error_summary && <p role="alert" className="text-red-700">{context.error_summary}</p>}
    {context.status === "SUCCESS_WITH_WARNINGS" && <p role="status" className="text-amber-800">Completed with warnings. {context.gold_skip_reason}</p>}
    {context.quarantine_available && <p className="text-amber-800">Quarantine available · {context.rejected_rows ?? "—"} rejected rows. {context.issue_summary.map((issue) => `${issue.rule_type}: ${issue.violation_count}`).join(" · ")}</p>}
    {context.status === "SUCCESS" && <p role="status" className="text-emerald-700">Processing complete.{(context.rejected_rows ?? 0) > 0 ? " Completed with rejected rows; review the quarantine summary." : ""}</p>}
    {context.latest_attempt && <p className="break-words text-xs text-slate-500">Latest attempt #{context.latest_attempt.id} · {context.latest_attempt.stage} · {format(context.latest_attempt.status)}</p>}
    {context.started_at && <p className="text-xs text-slate-500">Started <time dateTime={context.started_at}>{new Date(context.started_at).toLocaleString()}</time></p>}
    {context.completed_at && <p className="text-xs text-slate-500">Completed <time dateTime={context.completed_at}>{new Date(context.completed_at).toLocaleString()}</time></p>}
  </div>
}
