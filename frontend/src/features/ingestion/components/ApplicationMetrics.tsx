import type { ApplicationMetrics as Metrics } from "../../../services/api/incremental"
const labels: [keyof Metrics, string][] = [
  ["input_rows","Input Rows"], ["valid_rows","Valid Rows"], ["inserted_rows","Inserted"],
  ["updated_rows","Updated"], ["unchanged_rows","Unchanged"], ["duplicate_rows","Duplicates"],
  ["rejected_rows","Quarantined"], ["deactivated_rows","Deactivated"], ["current_state_rows","Current Dataset Rows"],
]
/** Application metrics only; do not substitute per-file Gold rows for dataset state. */
export function ApplicationMetrics({ metrics }: { metrics: Metrics }) {
  return <dl className="grid gap-3 text-sm sm:grid-cols-3">{labels.map(([field,label]) => <div key={field}><dt className="text-slate-500">{label}</dt><dd>{metrics[field]?.toLocaleString() ?? "—"}</dd></div>)}</dl>
}
