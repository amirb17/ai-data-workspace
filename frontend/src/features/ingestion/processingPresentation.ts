import type { DatasetProcessing, ProcessingContext } from "../../services/api/rules"

export const terminalDelivery = (c: ProcessingContext) => ["SUCCESS", "SUCCESS_WITH_WARNINGS"].includes(c.status)
export const warningDelivery = (c: ProcessingContext) => c.status === "SUCCESS_WITH_WARNINGS" || (c.status === "SUCCESS" && ((c.rejected_rows ?? 0) > 0 || (c.incremental_rejected_rows ?? 0) > 0 || (c.stale_rows ?? 0) > 0))
export const countLabel = (value: number | null | undefined) => value != null && Number.isFinite(value) && value >= 0 ? value.toLocaleString() : "—"
const issueLabels: Record<string, string> = { NOT_NULL: "Missing required values", DATA_TYPE: "Invalid data types", UNIQUE: "Duplicate keys" }
export const issueLabel = (type: string) => issueLabels[type] ?? type.replaceAll("_", " ").toLowerCase()
export function rateLabel(value: number | null, total: number | null) {
  return value != null && total != null && Number.isFinite(value) && Number.isFinite(total) && total > 0 && value >= 0 && value <= total
    ? `${(100 * value / total).toFixed(1)}%` : "—"
}
export function deliveryStatus(c: ProcessingContext) {
  if (warningDelivery(c)) return { label: "Completed with warnings", variant: "warning" as const }
  if (c.status === "SUCCESS") return { label: "Completed successfully", variant: "success" as const }
  if (c.status === "AWAITING_RULES") return { label: "Rules need your review", variant: "warning" as const }
  if (c.status.endsWith("_FAILED")) return { label: c.status === "GOLD_FAILED" ? "Gold publication failed" : "Processing needs attention", variant: "error" as const }
  if (c.status.endsWith("_PROCESSING")) return { label: "Processing", variant: "processing" as const }
  if (c.status === "READY_TO_PROCESS") return { label: "Ready to process", variant: "neutral" as const }
  if (c.status === "DATASET_UPDATE_BLOCKED") return { label: "Review Contract", variant: "warning" as const }
  if (["READY_FOR_SILVER", "READY_FOR_GOLD", "READY_TO_APPLY"].includes(c.status)) return { label: "Ready to continue", variant: "neutral" as const }
  return { label: "State unavailable", variant: "neutral" as const }
}
export function overviewWarnings(data: DatasetProcessing) {
  return data.deliveries.filter(d => warningDelivery(d.context)).length
}
export function overviewAttention(data: DatasetProcessing) {
  // Backend attention includes terminal warnings but not mixed SUCCESS outcomes.
  return data.summary.needs_attention + data.deliveries.filter(d => d.context.status === "SUCCESS" && (d.context.rejected_rows ?? 0) > 0).length
}
export function datasetReadyMessage(data: DatasetProcessing) {
  if (!data.deliveries.length) return "No deliveries yet"
  if (data.summary.pending > 0 || data.summary.processing > 0) return null
  if (overviewAttention(data) > 0) return "No pending deliveries. Review deliveries that need attention."
  return data.deliveries.every(d => terminalDelivery(d.context)) ? "Everything is up to date. All current deliveries have been processed." : "No pending deliveries. Refresh state or review delivery details."
}
export const ownsProcessing = (data: DatasetProcessing | undefined, w: string, d: string) =>
  data?.workspace_id === Number(w) && data.dataset_id === Number(d)
