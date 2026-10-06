import { Link } from "react-router-dom"
import { Card } from "../../../components/ui/Card"
import { StatusBadge } from "../../../components/ui/StatusBadge"
import type { WorkspaceListItem } from "../types"
export function WorkspaceCard({ workspace }: { workspace: WorkspaceListItem }) {
  return <Card className="space-y-4 p-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><h2 className="break-words text-lg font-semibold [overflow-wrap:anywhere]">{workspace.name}</h2><StatusBadge status={workspace.status} /></div>
    {workspace.description && <p className="break-words text-sm leading-6 text-slate-600">{workspace.description}</p>}
    {workspace.datasetCount != null && <p className="text-sm text-slate-600">{workspace.datasetCount} datasets</p>}
    {workspace.updatedAt !== "Unavailable" && <p className="text-xs text-slate-500">Updated {new Date(workspace.updatedAt).toLocaleString()}</p>}
    <Link className="inline-flex min-h-11 items-center font-medium text-indigo-700" to={`/app/workspaces/${workspace.id}`}>Open Workspace<span className="sr-only">: {workspace.name}</span></Link>
  </Card>
}
