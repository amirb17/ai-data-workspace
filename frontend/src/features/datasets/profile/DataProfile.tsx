import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiFeedback } from '../../../components/ui/ApiFeedback'
import { useApiResource } from '../../../services/api/useApiResource'
import { getDatasetProfile,getProfileReadiness,refreshDatasetProfile,type DatasetProfile } from '../../../services/api/profile'
import { ColumnEvidenceCard } from './ColumnEvidenceCard'
const labels = {NOT_PROFILED:'Not profiled',STALE:'Dataset changed — refresh profile',PROFILING:'Profiling trusted data…',READY:'Profile ready',FAILED:'Profile needs attention'}
export function ProfileEvidence({data,busy=false,error='',refresh}: {data: DatasetProfile; busy?: boolean; error?: string; refresh: () => void}) {
  const summary=data.profile_ready ? data.summary : null
  return <div className="min-w-0 space-y-5">
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
      <h2 className="text-lg font-semibold">Data Profile</h2><p role="status">{labels[data.freshness]}</p>
      <p className="text-sm text-slate-600">Factual statistics and potential patterns from trusted dataset state. Evidence does not approve identifiers or assign business meaning.</p>
      {data.current_state_id == null && <p className="text-sm">Complete a dataset update before profiling.</p>}
      {data.freshness==='FAILED' && <p className="text-sm text-amber-800">Profiling could not complete. Trusted data and previous evidence are safe; retry this profile.</p>}
      {data.profile_timestamp && <p className="text-sm">Last profile built: {new Date(data.profile_timestamp).toLocaleString()}</p>}
      {error && <p role="alert" className="text-sm text-amber-800">{error}</p>}
      {!data.profile_ready && data.current_state_id != null && <button type="button" className="min-h-11 rounded-lg bg-indigo-600 px-4 text-white disabled:opacity-50" disabled={busy || !data.can_refresh} onClick={refresh}>{busy || data.freshness==='PROFILING' ? 'Profiling…' : data.freshness==='FAILED' ? 'Retry Profile' : 'Refresh Profile'}</button>}
    </section>
    {summary && <><dl className="grid grid-cols-2 gap-3 md:grid-cols-3">{[['Rows',summary.row_count],['Columns',summary.column_count],['Potential identifiers',summary.identifier_candidates.length],['Date/time columns',summary.datetime_candidates.length],['Numeric columns',summary.numeric_columns.length],['Potential sensitive fields',summary.potential_sensitive_fields.length]].map(([label,value])=><div className="min-w-0 rounded-xl border border-slate-200 bg-white p-4" key={label}><dt className="text-sm text-slate-600">{label}</dt><dd className="text-xl font-semibold">{value.toLocaleString()}</dd></div>)}</dl>
      <section className="space-y-2 text-sm [overflow-wrap:anywhere]"><p>{summary.load_strategy} · Dataset state {data.state_version} · Schema {data.schema_version} · Profile {data.profile_version}</p><p>Configured business key: {summary.business_key.join(' + ') || 'Not configured'}</p><p>Configured event time: {summary.event_time_column || 'Not configured'}</p>{summary.snapshot_effective_at && <p>Configured snapshot effective time: {summary.snapshot_effective_at}</p>}{summary.load_strategy==='SNAPSHOT' && <p>Profiles include active records only: {summary.active_rows} active, {summary.inactive_rows} inactive retained in history.</p>}</section>
      <div className="grid gap-4 xl:grid-cols-2">{data.columns.map(column=><ColumnEvidenceCard key={column.original_name} column={column} />)}</div></>}
  </div>
}
export function DataProfile({workspaceId,datasetId}: {workspaceId: string; datasetId: string}) {
  const load=useCallback((signal: AbortSignal)=>getDatasetProfile(workspaceId,datasetId,signal),[workspaceId,datasetId])
  const resource=useApiResource(`profile:${workspaceId}:${datasetId}`,load)
  return resource.data ? <ProfileContent key={`${workspaceId}:${datasetId}`} data={resource.data} workspaceId={workspaceId} datasetId={datasetId} reload={resource.retry} /> : <ApiFeedback loading="Checking dataset profile…" error={resource.error} retry={resource.retry} />
}
function ProfileContent({data,workspaceId,datasetId,reload}: {data: DatasetProfile; workspaceId: string; datasetId: string; reload: () => void}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('')
  const accepting=useRef(false),mounted=useRef(true)
  useEffect(()=>{mounted.current=true; return ()=>{mounted.current=false}},[])
  useEffect(()=>{
    const controller=new AbortController()
    const interval=setInterval(()=>{getProfileReadiness(workspaceId,datasetId,controller.signal).then(next=>{
      if (!controller.signal.aborted && (next.freshness!==data.freshness || next.profile_id!==data.profile_id || next.current_state_id!==data.current_state_id)) reload()
    },()=>{if (!controller.signal.aborted) reload()})},3000)
    return ()=>{clearInterval(interval);controller.abort()}
  },[data.freshness,data.profile_id,data.current_state_id,workspaceId,datasetId,reload])
  async function refresh() {
    if (accepting.current) return
    accepting.current=true;setBusy(true);setError('')
    try {await refreshDatasetProfile(workspaceId,datasetId)}
    catch {if(mounted.current)setError('Profile could not be refreshed. Reload the status and retry.')}
    finally {accepting.current=false;if(mounted.current){setBusy(false);reload()}}
  }
  return <ProfileEvidence data={data} busy={busy} error={error} refresh={refresh} />
}
