import { StatusBadge } from "../../../components/ui/StatusBadge"
import type { DatasetFileStatus } from "../types"
export function FileStatusBadge({ status }: { status: DatasetFileStatus }) { return <StatusBadge status={status} /> }
