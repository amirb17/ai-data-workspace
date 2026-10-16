import { CheckCircle2, AlertTriangle, LoaderCircle, Circle } from "lucide-react"
import { Badge } from "./Badge"

import { statusLabel } from "../../utils/statusLabels"

export function StatusBadge({ status }: { status: string }) {
  const failed = status.endsWith("FAILED") || ["BREAKING", "WRONG_DATASET_LIKELY"].includes(status)
  const warning = ["WARNING", "SUCCESS_WITH_WARNINGS", "AWAITING_RULES", "NEEDS_ATTENTION", "REVIEW_REQUIRED", "STALE"].includes(status)
  const running = /PROCESSING|UPLOADING|INSPECTING/.test(status)
  const completed = ["SUCCESS", "PROCESSED", "FINALIZED", "MATCH", "CONFIGURED", "FRESH", "AUTO_ACCEPT"].includes(status)
  const Icon = failed || warning ? AlertTriangle : running ? LoaderCircle : completed ? CheckCircle2 : Circle
  return <Badge variant={failed ? "error" : warning ? "warning" : running ? "processing" : completed ? "success" : "neutral"}><Icon size={14} aria-hidden="true" className="mr-1 shrink-0" />{statusLabel(status)}</Badge>
}
