import {useCallback,useEffect,useRef,useState} from 'react'
import {Link} from 'react-router-dom'
import {Button} from '../../../components/ui/Button'
import {ApiFeedback} from '../../../components/ui/ApiFeedback'
import {useApiResource} from '../../../services/api/useApiResource'
import {capabilities,processingJobs,csvPreview,goldAssessment,previewReason,type Preview,type GoldAssessment} from '../../../services/api/product'
import {datasetProcessing} from '../../../services/api/rules'

type Props={workspaceId:string;datasetId:string}
export function ProductReadiness(props:Props){return <Readiness key={`${props.workspaceId}:${props.datasetId}`} {...props}/>}

function Readiness({workspaceId:w,datasetId:d}:Props){
  const load=useCallback((signal:AbortSignal)=>capabilities(w,d,signal),[w,d])
  const loadJobs=useCallback((signal:AbortSignal)=>processingJobs(w,d,signal),[w,d])
  const loadDeliveries=useCallback((signal:AbortSignal)=>datasetProcessing(w,d,false,signal),[w,d])
  const capability=useApiResource(`capabilities:${w}:${d}`,load)
  const jobs=useApiResource(`jobs:${w}:${d}`,loadJobs)
  const deliveries=useApiResource(`preview-deliveries:${w}:${d}`,loadDeliveries)
  const [upload,setUpload]=useState(''),[file,setFile]=useState<File|null>(null),[mode,setMode]=useState<'quality'|'exact'>('quality')
  const [preview,setPreview]=useState<Preview>(),[assessment,setAssessment]=useState<GoldAssessment>(),[model,setModel]=useState('')
  const [busy,setBusy]=useState(false),[error,setError]=useState('')
  const active=useRef<AbortController|null>(null)
  useEffect(()=>()=>active.current?.abort(),[])
  function reset(){active.current?.abort();setBusy(false);setPreview(undefined);setAssessment(undefined);setError('')}
  async function run(kind:'csv'|'gold'){
    if(busy||!upload)return
    const controller=new AbortController();active.current=controller
    setBusy(true);setError('');setPreview(undefined);setAssessment(undefined)
    try{
      if(kind==='csv'&&file){const value=await csvPreview(w,d,Number(upload),file,mode,controller.signal);if(!controller.signal.aborted)setPreview(value)}
      if(kind==='gold'&&model){const selected=capability.data?.warehouse_models.find(m=>String(m.version_id)===model);if(selected){const value=await goldAssessment(w,d,Number(upload),selected.model_id,selected.version_id,controller.signal);if(!controller.signal.aborted)setAssessment(value)}}
    }catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:'Preview unavailable.')}
    finally{if(!controller.signal.aborted)setBusy(false)}
  }
  if(!capability.data)return <ApiFeedback error={capability.error} retry={capability.retry} loading="Loading product capabilities…"/>
  const base=`/app/workspaces/${w}/datasets/${d}`
  return <section className="space-y-5" aria-label="Dataset readiness and previews">
    <header><h2 className="text-2xl font-semibold">Readiness & previews</h2><p className="mt-2 text-sm text-slate-600">Preview, approval, processing, verification and publication are separate decisions. These controls never submit or publish a job.</p></header>
    <div className="grid gap-3 rounded-xl border bg-white p-5 sm:grid-cols-3"><p>{capability.data.csv_upload?'CSV upload supported':'CSV upload unavailable in this release'}<br/><span className="text-sm text-slate-600">XLSX is not yet supported.</span></p><p>Processing submission disabled<br/><span className="text-sm text-slate-600">Runtime approval is still pending.</span></p><p>Warehouse: {capability.data.warehouse.replaceAll('_',' ').toLowerCase()}<br/><span className="text-sm text-slate-600">Gold alone does not authorize a warehouse load.</span></p></div>
    <nav className="flex flex-wrap gap-4 text-sm text-indigo-700" aria-label="Dataset preparation"><Link to={`${base}/files`}>Upload CSV & inspect schema</Link><Link to={`${base}/rules`}>Review rules</Link><Link to={`${base}/data-quality`}>Published quality results</Link><Link to={`${base}/processing`}>Delivery stage history</Link><Link to={`${base}/analytics`}>Supported analytics</Link></nav>
    <section className="space-y-4 rounded-xl border bg-white p-5"><h3 className="font-semibold">Server preview</h3><p className="text-sm text-slate-600">Select an owned completed delivery and the identical original CSV (up to 2 MiB). The server checks its registered version and approved rules. No rows or artifact bytes are returned.</p>
      {!deliveries.data?<ApiFeedback error={deliveries.error} retry={deliveries.retry} loading="Loading completed deliveries…"/>:deliveries.data.deliveries.length===0?<p>No completed deliveries yet. Upload a CSV from Files.</p>:<>
      <label className="block text-sm">Delivery<select className="mt-1 block min-h-11 w-full rounded border p-2" value={upload} onChange={e=>{reset();setUpload(e.target.value)}}><option value="">Choose a delivery</option>{deliveries.data.deliveries.map(x=><option key={x.context.upload_request_id} value={x.context.upload_request_id}>{x.source_file_name}</option>)}</select></label>
      <label className="block text-sm">Original CSV<input className="mt-1 block min-h-11 w-full" type="file" accept=".csv,text/csv" onChange={e=>{reset();setFile(e.target.files?.[0]??null)}}/></label>
      <label className="block text-sm">Preview type<select className="mt-1 block min-h-11 w-full rounded border p-2" value={mode} onChange={e=>{reset();setMode(e.target.value as 'quality'|'exact')}}><option value="quality">Approved quality checks</option><option value="exact">Strict monetary precision</option></select></label>
      <Button disabled={busy||!upload||!file} onClick={()=>void run('csv')}>{busy?'Checking…':'Run preview'}</Button>
      {capability.data.warehouse_models.length>0&&<div className="space-y-3 border-t pt-4"><label className="block text-sm">Approved warehouse model<select className="mt-1 block min-h-11 w-full rounded border p-2" value={model} onChange={e=>{reset();setModel(e.target.value)}}><option value="">Choose a model version</option>{capability.data.warehouse_models.map(m=><option key={m.version_id} value={m.version_id}>{m.name} · Version {m.version_number}</option>)}</select></label><Button variant="secondary" disabled={busy||!upload||!model} onClick={()=>void run('gold')}>Assess Gold compatibility</Button></div>}
      </>}
      {error&&<p role="alert" className="text-sm text-red-700">{error}</p>}
      {preview&&<div role="status" className="space-y-2 border-t pt-4">{preview.status==='UNAVAILABLE'?<p>{previewReason(preview.reason)}</p>:<><p className="font-medium">Preview only · Approved rule version {preview.rule_version}</p><p>{preview.total_rows} source rows · {preview.valid_rows} valid · {preview.rejected_rows} quarantined</p><p className="break-words text-sm">Columns: {preview.columns.join(', ')}</p><ul className="text-sm">{preview.issues.map((i,n)=><li key={n}>{i.column}: {i.rule.replaceAll('_',' ').toLowerCase()} — {i.count} violations</li>)}</ul><p className="text-sm text-slate-600">This result is not a processing receipt, approval or publication.</p></>}</div>}
      {assessment&&<p role="status">{assessment.status==='UNAVAILABLE'?previewReason(assessment.reason):`${assessment.rows} rows match the projection. Assessment only; warehouse admission and reconciliation are still required.`}</p>}
    </section>
    <section className="space-y-3 rounded-xl border bg-white p-5"><h3 className="font-semibold">Durable job state</h3>{!jobs.data?<ApiFeedback error={jobs.error} retry={jobs.retry} loading="Loading job state…"/>:jobs.data.jobs.length===0?<p>No durable jobs recorded for this dataset. This does not mean a delivery has been processed.</p>:<ul className="space-y-2 text-sm">{jobs.data.jobs.map(j=><li className="flex flex-wrap justify-between gap-2 border-t py-2" key={j.job_id}><span>{j.operation} · {j.status.replaceAll('_',' ')}</span><span>{j.attempt_count} attempts</span></li>)}</ul>}<Button variant="secondary" onClick={jobs.retry}>Refresh jobs</Button></section>
  </section>
}
