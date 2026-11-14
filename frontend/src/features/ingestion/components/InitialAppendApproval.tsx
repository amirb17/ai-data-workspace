import {useRef,useState} from 'react'
import {useReleaseFeatures} from '../../../services/releaseFeatures'
import {approveInitialAppend} from '../../../services/api/incremental'
import {continueProcessing} from '../../../services/api/rules'
export function InitialAppendApproval({workspaceId,datasetId,uploadId,disabled,reload}:{workspaceId:string;datasetId:string;uploadId:number;disabled:boolean;reload:()=>void}){
 const features=useReleaseFeatures(),active=useRef(false)
 const [confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('')
 async function approve(){
  if(active.current||disabled||!confirmed||!features.initialAppend||!features.processing)return
  active.current=true;setBusy(true);setError('')
  try{await approveInitialAppend(workspaceId,datasetId,uploadId);await continueProcessing(workspaceId,datasetId,uploadId);reload()}
  catch(caught){setError(caught instanceof Error?caught.message:'Unable to publish. Refresh delivery status before retrying.');reload()}
  finally{active.current=false;setBusy(false)}
 }
 return <section className="space-y-3 rounded-xl border border-indigo-200 bg-indigo-50 p-4" aria-label="First dataset publication"><h4 className="font-semibold text-indigo-950">Publish your first trusted dataset</h4><p className="text-sm text-indigo-900">Silver validation is complete. APPEND adds valid records under a strict schema. It does not update records by business key.</p>{features.initialAppend?<><label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" checked={confirmed} disabled={disabled||busy} onChange={e=>setConfirmed(e.target.checked)}/>Use APPEND for this dataset and publish these valid records.</label><button type="button" className="min-h-11 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white disabled:opacity-50" disabled={disabled||busy||!confirmed||!features.processing} onClick={()=>void approve()}>{busy?'Submitting publication...':'Approve APPEND and publish'}</button></>:<p role="status" className="text-sm">Initial publication awaits load policy approval.</p>}{error&&<p role="alert" className="text-sm text-red-700">{error}</p>}</section>
}
