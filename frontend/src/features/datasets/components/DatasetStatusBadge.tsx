import { StatusBadge } from "../../../components/ui/StatusBadge"
import type { DatasetStatus } from "../types"
export function DatasetStatusBadge({ status }: { status: DatasetStatus }) { return <StatusBadge status={status} /> }
