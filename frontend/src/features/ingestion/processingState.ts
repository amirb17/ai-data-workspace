import type { ProcessingContext } from "../../services/api/rules"

export const isRunning = (status: string) => ["BRONZE_PROCESSING", "SILVER_PROCESSING", "GOLD_PROCESSING"].includes(status)
export function executionAction(context: ProcessingContext) {
  if (["SUCCESS", "SUCCESS_WITH_WARNINGS"].includes(context.status)) return null
  if (context.can_continue && context.status.endsWith("_FAILED")) return "Retry Processing"
  if (["SILVER_PROCESSING", "GOLD_PROCESSING"].includes(context.status)) return "Recover Interrupted Processing"
  return null
}
