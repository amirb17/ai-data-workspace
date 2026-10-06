import { CheckCircle2, Circle, LoaderCircle, AlertTriangle, XCircle, MinusCircle } from "lucide-react"
import type { ProcessingContext } from "../../../services/api/rules"

const descriptions = { bronze: "Reading and profiling data", rules: "Your approved validation decisions", silver: "Validating and cleaning rows", dataset_update: "Applying accepted records to the trusted dataset", gold: "Preparing delivery analytics output" }
function stageView(stage: keyof typeof descriptions, c: ProcessingContext) {
  const state = c.stages[stage]
  if (state === "BLOCKED") return { label: "Review Contract", Icon: AlertTriangle, color: "text-amber-800" }
  if (state === "SKIPPED") return { label: "Skipped", Icon: MinusCircle, color: "text-slate-600" }
  if (state === "FAILED" || state === "CRASHED" || c.status === `${stage.toUpperCase()}_FAILED`) return { label: "Failed", Icon: XCircle, color: "text-red-700" }
  if (state === "SUCCESS" || state === "FINALIZED") return { label: "Complete", Icon: CheckCircle2, color: "text-emerald-700" }
  if (state === "PROCESSING") return { label: "Processing", Icon: LoaderCircle, color: "text-indigo-700" }
  if (stage === "rules" && c.status === "AWAITING_RULES") return { label: "Needs attention", Icon: AlertTriangle, color: "text-amber-800" }
  return { label: state === "PENDING" || state === "DRAFT" ? "Pending" : "Unavailable", Icon: Circle, color: "text-slate-500" }
}
export function ProcessingStages({ context, compact = false }: { context: ProcessingContext; compact?: boolean }) {
  return <ol aria-label="Processing stages" className={`grid grid-cols-1 gap-3 ${context.stages.dataset_update != null ? "sm:grid-cols-5" : "sm:grid-cols-4"} ${compact ? "text-xs" : "text-sm"}`}>
    {(Object.keys(descriptions) as (keyof typeof descriptions)[]).filter(stage => stage !== "dataset_update" || context.stages.dataset_update != null).map(stage => {
      const { label, Icon, color } = stageView(stage, context)
      const description = stage === "rules" && context.rule_state === "FINALIZED" && context.rule_version != null
        ? `Approved Rule Version ${context.rule_version}${context.rules_reused ? " reused" : ""}` : descriptions[stage]
      return <li key={stage} className={compact ? "flex flex-wrap items-center gap-2" : "rounded-lg border border-slate-200 bg-white p-3"}>
        <div className={`flex items-center gap-2 ${color}`}><Icon size={16} aria-hidden="true" className="shrink-0" /><span className="capitalize font-semibold">{stage === "dataset_update" ? "Dataset Update" : stage}</span></div>
        {!compact && <p className="mt-2 text-xs leading-5 text-slate-600">{description}</p>}
        <p className={`${compact ? "" : "mt-2"} ${color}`}>{label}{compact && stage === "rules" && context.rule_state === "FINALIZED" && context.rules_reused ? ` · Approved Rule Version ${context.rule_version} reused` : ""}</p>
      </li>
    })}
  </ol>
}
