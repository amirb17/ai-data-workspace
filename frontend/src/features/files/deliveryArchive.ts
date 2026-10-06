import type { ProcessingContext } from "../../services/api/rules"
export function archiveAction(context: ProcessingContext) {
  const blocked = context.status === "PROCESSING" || context.status.endsWith("_PROCESSING")
  const processed = context.stages.silver === "SUCCESS" || context.stages.gold === "SUCCESS" || context.status === "SUCCESS" || context.status === "SUCCESS_WITH_WARNINGS"
  return { blocked, processed, label: processed ? "Archive Delivery" : "Remove Delivery" }
}
