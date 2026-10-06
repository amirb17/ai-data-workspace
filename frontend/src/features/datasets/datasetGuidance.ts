import type { DatasetProcessing } from "../../services/api/rules"
export function datasetGuidance(data: DatasetProcessing, contractConfigured: boolean) {
  if (!data.deliveries.length) return { label: "Add Delivery", section: "files", message: "Add your first CSV delivery to define the expected structure." }
  if (data.deliveries.some(d => d.context.status === "DATASET_UPDATE_BLOCKED")) return { label: "Review Contract", section: "contract", message: "Dataset update requires a compatible APPEND policy. Review the configured schema and strategy." }
  if (!contractConfigured && !data.deliveries.some(d => d.context.load_strategy)) return { label: "Configure Contract", section: "contract", message: "Configure the expected structure for future deliveries." }
  const awaiting = data.deliveries.find(d => d.context.status === "AWAITING_RULES")
  if (awaiting) return { label: "Review Rules", section: `rules?upload=${awaiting.context.upload_request_id}`, message: "Processing is waiting for your validation decisions." }
  if (data.summary.pending || data.summary.processing || data.summary.failed) return { label: "Go to Processing", section: "processing", message: data.summary.failed ? "Review the delivery that needs attention before retrying." : "Follow pending deliveries through processing." }
  if (data.deliveries.some(d => (d.context.rejected_rows ?? 0) > 0)) return { label: "View Data Quality", section: "data-quality", message: "Some rows need attention. Review the quality summary before exploring analytics." }
  if (data.deliveries.some(d => d.context.stages.gold === "SUCCESS")) return { label: "View Analytics", section: "analytics", message: "Processing output is available. Check analytics readiness." }
  return { label: "View Data Quality", section: "data-quality", message: "Review processing outcomes and rows that need attention." }
}
