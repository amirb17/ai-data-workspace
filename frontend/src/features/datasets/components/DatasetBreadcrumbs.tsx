import { Link } from "react-router-dom"

export function DatasetBreadcrumbs({ workspaceId, datasetId, workspaceName, datasetName, section }: { workspaceId: string; datasetId: string; workspaceName: string; datasetName: string; section: string }) {
  const items = [{ label: "Workspaces", to: "/app/workspaces" }, { label: workspaceName, to: `/app/workspaces/${workspaceId}` },
    { label: datasetName, to: `/app/workspaces/${workspaceId}/datasets/${datasetId}` }]
  return <nav aria-label="Breadcrumb" className="text-sm text-slate-600">
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-1">
      {items.map(item => <li key={item.to} className="flex min-w-0 max-w-full items-center gap-2"><Link to={item.to} className="inline-flex min-h-11 min-w-0 items-center rounded px-1 hover:text-indigo-700 focus-visible:outline focus-visible:outline-2"><span className="break-words [overflow-wrap:anywhere]">{item.label}</span></Link><span aria-hidden="true">›</span></li>)}
      <li aria-current="page" className="break-words font-medium text-slate-950">{section}</li>
    </ol>
  </nav>
}
