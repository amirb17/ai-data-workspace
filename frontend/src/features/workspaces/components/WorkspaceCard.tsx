import {
  ArrowRight,
  BarChart3,
  Database,
  Workflow,
} from "lucide-react"
import { Link } from "react-router-dom"

import { Badge } from "../../../components/ui/Badge"
import { Card } from "../../../components/ui/Card"
import type {
  WorkspaceListItem,
  WorkspaceStatus,
} from "../types"

type WorkspaceCardProps = {
  workspace: WorkspaceListItem
}

function getStatusVariant(
  status: WorkspaceStatus,
) {
  switch (status) {
    case "ACTIVE":
      return "success"

    case "PROCESSING":
      return "processing"

    case "NEEDS_ATTENTION":
      return "warning"

    default:
      return "neutral"
  }
}

function formatStatus(
  status: WorkspaceStatus,
) {
  return status
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (value) =>
      value.toUpperCase(),
    )
}

export function WorkspaceCard({
  workspace,
}: WorkspaceCardProps) {
  return (
    <Card className="group overflow-hidden p-5 transition hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
            <Database size={19} />
          </div>

          <h2 className="mt-4 text-lg font-semibold text-slate-950">
            {workspace.name}
          </h2>

          <p className="mt-2 text-sm leading-6 text-slate-500">
            {workspace.description}
          </p>
        </div>

        <Badge
          variant={getStatusVariant(
            workspace.status,
          )}
        >
          {formatStatus(
            workspace.status,
          )}
        </Badge>
      </div>

      <div className="mt-6 grid grid-cols-3 gap-3 border-t border-slate-100 pt-4">
        <div>
          <div className="flex items-center gap-1.5 text-slate-500">
            <Database size={14} />

            <span className="text-xs">
              Datasets
            </span>
          </div>

          <p className="mt-1 text-sm font-semibold text-slate-900">
            {workspace.datasetCount}
          </p>
        </div>

        <div>
          <div className="flex items-center gap-1.5 text-slate-500">
            <BarChart3 size={14} />

            <span className="text-xs">
              Ready
            </span>
          </div>

          <p className="mt-1 text-sm font-semibold text-slate-900">
            {workspace.analyticsReadyCount}
          </p>
        </div>

        <div>
          <div className="flex items-center gap-1.5 text-slate-500">
            <Workflow size={14} />

            <span className="text-xs">
              Processing
            </span>
          </div>

          <p className="mt-1 text-sm font-semibold text-slate-900">
            {workspace.processingCount}
          </p>
        </div>
      </div>

      <div className="mt-5 flex items-center justify-between">
        <p className="text-xs text-slate-500">
          Updated {workspace.updatedAt}
        </p>

        <Link
          to={`/app/workspaces/${workspace.id}`}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-indigo-600 transition group-hover:text-indigo-700"
        >
          Open workspace
          <ArrowRight size={15} />
        </Link>
      </div>
    </Card>
  )
}