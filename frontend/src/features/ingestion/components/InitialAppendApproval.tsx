import {useEffect,useRef,useState} from 'react'
import {authConfig} from '../../../services/api/auth'
import {approveInitialAppend} from '../../../services/api/incremental'
import {continueProcessing} from '../../../services/api/rules'

export function InitialAppendApproval({workspaceId,datasetId,uploadId,disabled,reload}:{workspaceId:string;datasetId:string;uploadId:number;disabled:boolean;reload:()=>void}) {
 const [enabled,setEnabled]=useState(false),[confirmed,setConfirmed]=useState(false)
 const [busy,setBusy]=useState(false),[error,setError]=useState('')
 const active=useRef(false)
 useEffect(()=>{let live=true;authConfig().then(value=>{if(live)setEnabled(value.initial_append_available===true&&value.processing_available===true)}).catch(()=>{if(live)setEnabled(false)});return()=>{live=false}},[])
 async function approve() {
  if(!enabled||!confirmed||busy||disabled||active.current)return
  active.current=true;setBusy(true);setError('')
  try {await approveInitialAppend(workspaceId,datasetId,uploadId);await continueProcessing(workspaceId,datasetId,uploadId);reload()}
  catch(caught){setError(caught instanceof Error?caught.message:'Publication failed. Refresh the delivery and retry.');reload()}
  finally {active.current=false;setBusy(false)}
 }
 return <section className="space-y-3 rounded-xl border border-indigo-200 bg-indigo-50 p-4" aria-label="First dataset publication">
  <h4 className="font-semibold text-indigo-950">Publish your first trusted dataset</h4>
  <p className="text-sm text-indigo-900">Silver validation is complete. APPEND adds valid rows under the approved schema; it does not update existing records.</p>
  {enabled?<><label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" checked={confirmed} disabled={busy||disabled} onChange={event=>setConfirmed(event.target.checked)}/>Approve initial APPEND publication for this dataset.</label>
   <button type="button" className="min-h-11 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white disabled:opacity-50" disabled={!confirmed||busy||disabled} onClick={()=>void approve()}>{busy?'Submitting publication…':'Approve APPEND and publish'}</button></>
   :<p role="status" className="text-sm">Initial publication is awaiting backend approval.</p>}
  {error&&<p role="alert" className="text-sm text-red-700">{error}</p>}
 </section>
}
