import { Badge } from "../../../components/ui/Badge"
import type { DatasetStatus } from "../types"

type DatasetStatusBadgeProps = {
  status: DatasetStatus
}

function getVariant(
  status: DatasetStatus,
) {
  switch (status) {
    case "READY":
      return "success"

    case "PROCESSING":
      return "processing"

    case "FAILED":
      return "error"

    case "NEEDS_ATTENTION":
      return "warning"
    
    case "EMPTY":
      return "neutral"

    default:
      return "neutral"
  }
}

function formatStatus(
  status: DatasetStatus,
) {
  return status
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (value) =>
      value.toUpperCase(),
    )
}

export function DatasetStatusBadge({
  status,
}: DatasetStatusBadgeProps) {
  return (
    <Badge variant={getVariant(status)}>
      {formatStatus(status)}
    </Badge>
  )
}