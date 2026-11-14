import {Link} from 'react-router-dom'
import {ArrowUpRight,Database} from 'lucide-react'
import {StatusBadge} from '../../../components/ui/StatusBadge'
import type {WorkspaceListItem} from '../types'
export function WorkspaceCard({workspace}:{workspace:WorkspaceListItem}){
 return <Link to={`/app/workspaces/${workspace.id}`} className="group block h-full rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-300 hover:shadow-md focus-visible:ring-2 focus-visible:ring-indigo-500 motion-reduce:transform-none motion-reduce:transition-none" aria-label={`Open workspace: ${workspace.name}`}>
  <div className="mb-4 flex items-center justify-between"><Database size={20} aria-hidden="true" className="text-indigo-600"/><StatusBadge status={workspace.status}/></div>
  <div className="flex items-start justify-between gap-3"><h2 className="break-words text-lg font-semibold text-slate-950 [overflow-wrap:anywhere]">{workspace.name}</h2><ArrowUpRight size={18} aria-hidden="true" className="mt-1 shrink-0 text-slate-400 group-hover:text-indigo-600"/></div>
  {workspace.description&&<p className="mt-2 line-clamp-2 break-words text-sm leading-6 text-slate-600">{workspace.description}</p>}
  <div className="mt-5 flex flex-wrap justify-between gap-2 border-t border-slate-100 pt-3 text-xs text-slate-500">{workspace.datasetCount!=null&&<span>{workspace.datasetCount} {workspace.datasetCount===1?'dataset':'datasets'}</span>}{workspace.updatedAt!=='Unavailable'&&<span>Updated {new Date(workspace.updatedAt).toLocaleDateString()}</span>}</div>
 </Link>
}
