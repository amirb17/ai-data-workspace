import {Link} from 'react-router-dom'
import {StatusBadge} from '../../../components/ui/StatusBadge'
import type {WorkspaceListItem} from '../types'
export function WorkspaceCard({workspace}:{workspace:WorkspaceListItem}){
 return <Link to={`/app/workspaces/${workspace.id}`} className="block h-full space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition-colors hover:border-indigo-300 focus-visible:ring-2 focus-visible:ring-indigo-500 motion-reduce:transition-none" aria-label={`Open workspace: ${workspace.name}`}>
  <div className="flex flex-wrap items-start justify-between gap-3"><h2 className="break-words text-lg font-semibold [overflow-wrap:anywhere]">{workspace.name}</h2><StatusBadge status={workspace.status}/></div>
  {workspace.description&&<p className="line-clamp-2 break-words text-sm leading-6 text-slate-600">{workspace.description}</p>}
  {workspace.datasetCount!=null&&<p className="text-sm text-slate-600">{workspace.datasetCount} {workspace.datasetCount===1?'dataset':'datasets'}</p>}
  {workspace.updatedAt!=='Unavailable'&&<p className="text-xs text-slate-500">Updated {new Date(workspace.updatedAt).toLocaleDateString()}</p>}
 </Link>
}
