import {useEffect,useRef,useState} from 'react'
import {ApiError} from '../../../services/api/client'
import {getWarehouseQueryResultReadiness, type WarehouseQueryResultReadiness} from '../../../services/api/warehouseCatalog'

/** Inspect already-owned warehouse attempts; never initiates a query or row fetch. */
export function WarehouseResultReadiness({workspace}:{workspace:string}) {
  const [handle,setHandle]=useState('')
  const [pending,setPending]=useState(false)
  const [result,setResult]=useState<WarehouseQueryResultReadiness|null>(null)
  const [message,setMessage]=useState('')
  const controller=useRef<AbortController|null>(null)
  useEffect(()=>()=>controller.current?.abort(),[])
  const valid=/^[a-f0-9]{32}$/.test(handle)

  async function inspect() {
    if (!valid) return
    controller.current?.abort()
    const current=new AbortController()
    controller.current=current
    setResult(null)
    setMessage('')
    setPending(true)
    try {
      const response=await getWarehouseQueryResultReadiness(workspace,handle,current.signal)
      if (!current.signal.aborted) setResult(response)
    } catch(error) {
      if (!current.signal.aborted) {
        const code=error instanceof ApiError?error.status:0
        setMessage(code===409
          ? 'This query is not eligible for results yet. It may still be running, unverified or unsettled.'
          : code===401||code===403
            ? 'Sign in again or check access to the original query session.'
            : code===404
              ? 'The query was not found for this workspace.'
              : 'Result readiness could not be verified. Try again after checking the handle.')
      }
    } finally {
      if (controller.current===current) {controller.current=null;setPending(false)}
    }
  }
  return <section aria-labelledby="warehouse-result-status-title" className="rounded-xl border border-slate-200 bg-white p-4">
    <h2 id="warehouse-result-status-title" className="text-base font-semibold text-slate-900">Warehouse query result status</h2>
    <p className="mt-2 text-sm text-slate-600">Check the readiness of an existing warehouse query from your current signed-in session. This does not run SQL or fetch result rows.</p>
    <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={event=>{event.preventDefault();void inspect()}}>
      <label className="min-w-0 flex-1 text-sm text-slate-700">Query attempt handle
        <input autoComplete="off" spellCheck={false} maxLength={32}
          className="mt-1 block min-h-11 w-full rounded-lg border border-slate-300 px-3 font-mono text-sm"
          placeholder="32-character query handle" value={handle}
          onChange={event=>{controller.current?.abort();controller.current=null;setPending(false);setHandle(event.target.value.trim());setResult(null);setMessage('')}}/>
      </label>
      <button type="submit" disabled={!valid||pending} className="min-h-11 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
        {pending?'Checking…':'Check result status'}
      </button>
    </form>
    {message&&<p role="alert" className="mt-3 text-sm text-amber-800">{message}</p>}
    {result&&<div role="status" className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
      <p className="font-medium text-slate-900">Verified successful query · accounting complete</p>
      <p className="mt-1 text-slate-600">Server result page limit: {result.max_page_rows} rows.</p>
      <p className="mt-1 text-amber-800">Warehouse result fetching is not released. No rows are available through this status check.</p>
    </div>}
  </section>
}
