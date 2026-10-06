import { CheckCircle2, AlertTriangle, LoaderCircle, Circle } from "lucide-react"
import { Badge } from "./Badge"

const labels: Record<string, string> = { ACTIVE: "Active", ARCHIVED: "Archived", READY: "Ready", EMPTY: "No deliveries yet", READY_TO_PROCESS: "Ready to process", READY_FOR_SILVER: "Ready to continue", READY_FOR_GOLD: "Ready to continue", AWAITING_RULES: "Rules need review", SUCCESS: "Completed", SUCCESS_WITH_WARNINGS: "Completed with warnings", FAILED: "Needs attention", NEEDS_ATTENTION: "Needs attention", PROCESSED: "Completed", UPLOADED: "Ready to process", FINALIZED: "Approved", DRAFT: "Not approved", MATCH: "Compatible", WARNING: "Compatible with warnings", BREAKING: "Contract mismatch", WRONG_DATASET_LIKELY: "This file may belong to another dataset", NO_CONTRACT: "No contract configured", CONFIGURED: "Configured", UNAVAILABLE: "Unavailable" }
function statusLabel(status: string) { return labels[status] ?? (status.endsWith("_FAILED") ? "Needs attention" : status.includes("PROCESSING") ? "Processing" : status.toLowerCase().replaceAll("_", " ")) }
export function StatusBadge({ status }: { status: string }) {
  const failed = status.endsWith("FAILED") || ["BREAKING", "WRONG_DATASET_LIKELY"].includes(status)
  const warning = ["WARNING", "SUCCESS_WITH_WARNINGS", "AWAITING_RULES", "NEEDS_ATTENTION"].includes(status)
  const running = /PROCESSING|UPLOADING|INSPECTING/.test(status)
  const completed = ["SUCCESS", "PROCESSED", "FINALIZED", "MATCH", "CONFIGURED"].includes(status)
  const Icon = failed || warning ? AlertTriangle : running ? LoaderCircle : completed ? CheckCircle2 : Circle
  return <Badge variant={failed ? "error" : warning ? "warning" : running ? "processing" : completed ? "success" : "neutral"}><Icon size={14} aria-hidden="true" className="mr-1 shrink-0" />{statusLabel(status)}</Badge>
}
