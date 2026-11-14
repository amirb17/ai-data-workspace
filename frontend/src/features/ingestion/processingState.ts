import type { ProcessingContext } from "../../services/api/rules"

export const isRunning = (status: string) => ["BRONZE_PROCESSING", "SILVER_PROCESSING", "GOLD_PROCESSING", "DATASET_UPDATE_PROCESSING"].includes(status)
export function executionAction(context: ProcessingContext) {
  if (context.archived_at) return null
  if (context.status === "READY_TO_PROCESS") return "Start Process"
  if (context.status === "READY_TO_APPLY" && context.can_continue) return "Publish trusted data"
  if (context.status === "READY_FOR_SILVER" && context.can_continue) return "Continue to Silver"
  if (context.status === "READY_FOR_GOLD" && context.can_continue) return "Continue to Gold"
  if (["SUCCESS", "SUCCESS_WITH_WARNINGS"].includes(context.status)) return null
  if (context.can_continue && context.status === "DATASET_UPDATE_FAILED") return "Retry Dataset Update"

  if (context.can_continue && context.status.endsWith("_FAILED")) return context.status === "GOLD_FAILED" ? "Retry Gold" : "Retry Processing"

  return null
}
