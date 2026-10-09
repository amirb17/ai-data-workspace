import { ProcessingStages } from "./ProcessingStages"
import { ProcessingResult } from "./ProcessingResult"
import type { ProcessingContext } from "../../../services/api/rules"

export function ProcessingDetails({ context, createdAt, qualityHref }: { context: ProcessingContext; createdAt?: string; qualityHref?: string }) {
  const metadata = [["Delivery ID / upload request", context.upload_request_id], ["Source file reference", context.file_id],
    ["Processing context reference", context.dataset_version_file_id], ["Dataset version", context.dataset_version_number == null ? null : `v${context.dataset_version_number}`],
    ["Applied rule version", context.rule_version], ["Latest attempt reference", context.latest_attempt?.id],
    ...(context.load_policy ? [["Policy version",context.load_policy.policy_version],["Business key",context.load_policy.business_keys.join(" + ")],["Change ordering",context.load_policy.event_time_column]] : []),
    ...(context.state_lineage ? [["Source state version",context.state_lineage.source_state_version],["Result state version",context.state_lineage.result_state_version]] : [])]
  const dates = [["Uploaded", createdAt], ["Processing started", context.started_at], ["Processing completed", context.completed_at],
    ...(context.application ? [["Dataset update started",context.application.started_at],["Dataset update completed",context.application.completed_at]] : []),
    ...(context.snapshot_context ? [["Effective snapshot time",context.snapshot_context.effective_at]] : [])]
  return <div className="space-y-6 border-t border-slate-200 pt-4 text-sm">
    <ProcessingStages context={context} />
    <ProcessingResult context={context} qualityHref={qualityHref} />
    {context.error_summary && <p role="alert" className="rounded-lg bg-red-50 p-3 text-red-800">{context.error_summary}</p>}
    <section aria-label="Delivery metadata" className="space-y-3">
      <h4 className="font-semibold text-slate-950">Delivery metadata</h4>
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {metadata.map(([label, value]) => <div key={label}><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 break-words">{value ?? "—"}</dd></div>)}
        {dates.map(([label, value]) => <div key={label}><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1">{value ? <time dateTime={value}>{new Date(value).toLocaleString()}</time> : "—"}</dd></div>)}
      </dl>
      {context.rule_state === "FINALIZED" && context.rule_version != null && <p className="text-slate-600">Approved Rule Version {context.rule_version}{context.rules_reused ? " reused" : " applied"}.</p>}
    </section>
  </div>
}
