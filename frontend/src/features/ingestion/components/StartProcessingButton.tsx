import {useRef,useState} from 'react'
import {useNavigate} from 'react-router-dom'
import {startBronze} from '../../../services/api/rules'
import {useReleaseFeatures} from '../../../services/releaseFeatures'
export function StartProcessingButton({workspaceId,datasetId,uploadId,onStarted}:{workspaceId:string;datasetId:string;uploadId:number;onStarted:()=>void}){
 const features=useReleaseFeatures(),navigate=useNavigate(),active=useRef(false)
 const [busy,setBusy]=useState(false),[error,setError]=useState('')
 async function start(){
  if(active.current||!features.processing)return
  active.current=true;setBusy(true);setError('')
  try {await startBronze(workspaceId,datasetId,uploadId);onStarted();navigate('/app/workspaces/'+workspaceId+'/datasets/'+datasetId+'/processing')}
  catch(caught){setError(caught instanceof Error?caught.message:'Unable to submit processing. Refresh status before retrying.')}
  finally{active.current=false;setBusy(false)}
 }
 return <div className="space-y-2"><button type="button" disabled={busy||!features.processing} onClick={()=>void start()} className="min-h-11 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white disabled:opacity-50">{busy?'Submitting Bronze...':'Start Process'}</button>{busy&&<p role="status" className="text-sm text-slate-600">Submitting a background job...</p>}{error&&<p role="alert" className="max-w-md text-sm text-red-700">{error}</p>}</div>
}
