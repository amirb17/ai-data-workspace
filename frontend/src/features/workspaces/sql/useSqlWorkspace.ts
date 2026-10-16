import {useCallback,useEffect,useRef,useState} from 'react'
import {sqlCatalog,sqlQueries,sqlHistory} from '../../../services/api/sqlAnalytics'
import type {SqlCatalog,SavedSql,SqlExecution} from '../../../services/api/sqlAnalytics'

export function useSqlWorkspace(workspace:string) {
  const [catalog,setCatalog]=useState<SqlCatalog|null>(null),[queries,setQueries]=useState<SavedSql[]>([]),[history,setHistory]=useState<SqlExecution[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[result,setResult]=useState<SqlExecution|null>(null)
  const alive=useRef(true),running=useRef(false),refreshing=useRef(0),requests=useRef(new Set<AbortController>())
  const refresh=useCallback(async(signal?:AbortSignal)=>{
    refreshing.current++
    const pending=[sqlCatalog(workspace,signal),sqlQueries(workspace,signal),sqlHistory(workspace,signal)] as const
    try {
    const [c,q,h]=await Promise.all(pending)
    if(!alive.current||signal?.aborted)return
    setError('')
    setCatalog(old=>JSON.stringify(old)===JSON.stringify(c)?old:c);setQueries(q);setHistory(h)
    setResult(old=>old?.status==='SUCCEEDED'&&(h.find(e=>e.execution_id===old.execution_id)?.freshness==='Data changed'||old.sources.some(s=>{const d=c.datasets.find(v=>v.dataset_id===s.dataset_id);return !d?.queryable||d.state_id!==s.state_id||d.gold_run_id!==s.gold_run_id}))?{...old,freshness:'Data changed'}:old)
    } finally {await Promise.allSettled(pending);refreshing.current--}
  },[workspace])
  useEffect(()=>{alive.current=true;const controller=new AbortController();requests.current.add(controller)
    void refresh(controller.signal).catch(()=>{if(!controller.signal.aborted)setError('Could not load SQL Analytics. Check API availability and the SQL metadata migration, then retry.')})
    const interval=window.setInterval(()=>{if(!running.current&&!refreshing.current)void refresh(controller.signal).catch(()=>{if(!controller.signal.aborted){setError('Could not refresh SQL sources. Retry before running.');setCatalog(null);setResult(null)}})},15000)
    const active=requests.current
    return()=>{alive.current=false;window.clearInterval(interval);active.forEach(c=>c.abort());active.clear()}
  },[refresh])
  async function action(task:(signal:AbortSignal)=>Promise<void>) {
    if(running.current)return
    running.current=true;setBusy(true);setError('');const controller=new AbortController();requests.current.add(controller)
    try {await task(controller.signal)}catch(e){if(alive.current&&!controller.signal.aborted)setError(e instanceof Error?e.message:'SQL action failed. Retry.')}
    finally {requests.current.delete(controller);running.current=false;if(alive.current)setBusy(false)}
  }
  return {catalog,queries,history,error,busy,result,setResult,refresh,action,retry:()=>action(async signal=>{await refresh(signal)})}
}
