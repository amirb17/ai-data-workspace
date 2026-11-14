import {useCallback} from 'react'
import {Link} from 'react-router-dom'
import {listWorkspaces} from '../../../services/api/workspaces'
import {useApiResource} from '../../../services/api/useApiResource'
import {ApiFeedback} from '../../../components/ui/ApiFeedback'
import {PageHeader} from '../../../components/ui/PageHeader'
export function WorkspaceDestination({title,description,section}:{title:string;description:string;section:string}){
 const load=useCallback((signal:AbortSignal)=>listWorkspaces(signal),[])
 const resource=useApiResource('destination:'+section,load)
 return <section className="space-y-6"><PageHeader title={title} description={description}/>{!resource.data?<ApiFeedback error={resource.error} retry={resource.retry} loading="Loading workspaces..."/>:resource.data.length?<div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{resource.data.filter(w=>w.status!=='ARCHIVED').map(w=><Link key={w.id} to={'/app/workspaces/'+w.id+'/'+section} className="rounded-xl border bg-white p-5 transition hover:border-indigo-300 hover:shadow-sm motion-reduce:transition-none"><h2 className="break-words text-lg font-semibold">{w.name}</h2><p className="mt-3 text-sm text-indigo-700">Open {title.toLowerCase()}</p></Link>)}</div>:<div className="rounded-xl border border-dashed p-6"><p className="text-sm text-slate-600">Create a workspace to get started.</p><Link to="/app/workspaces?create=true" className="mt-3 inline-flex min-h-11 items-center font-medium text-indigo-700">Create workspace</Link></div>}</section>
}
