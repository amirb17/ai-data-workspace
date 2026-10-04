import { Badge } from "../../../components/ui/Badge"
import type { DatasetFileStatus } from "../types"

type FileStatusBadgeProps = {
  status: DatasetFileStatus
}

function getVariant(
  status: DatasetFileStatus,
) {
  switch (status) {
    case "PROCESSED":
    case "READY_TO_PROCESS":
      return "success"

    case "UPLOADING":
    case "INSPECTING":
    case "PROCESSING":
      return "processing"

    case "NEEDS_MAPPING":
    case "NEEDS_ATTENTION":
      return "warning"

    case "FAILED":
      return "error"

    default:
      return "neutral"
  }
}

function formatStatus(
  status: DatasetFileStatus,
) {
  return status
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (value) =>
      value.toUpperCase(),
    )
}

export function FileStatusBadge({
  status,
}: FileStatusBadgeProps) {
  return (
    <Badge variant={getVariant(status)}>
      {formatStatus(status)}
    </Badge>
  )
}