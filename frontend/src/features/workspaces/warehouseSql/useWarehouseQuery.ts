import {useEffect,useRef,useState} from 'react'
import {usePublicReadOnly} from '../../../services/publicLaunchContext'
import {ApiError} from '../../../services/api/client'
import {admitWarehouseQuery,validateWarehouseQuery,warehouseQueryStatus,warehouseQueryResults,queryHandle} from '../../../services/api/warehouseQueries'
import type {WarehouseValidation,WarehouseStatus,WarehouseResults} from '../../../services/api/warehouseQueries'

export function useWarehouseQuery(workspace:string){
 const readOnly=usePublicReadOnly()
 const [model,setModel]=useState(0),[sql,setSql]=useState(''),[validation,setValidation]=useState<WarehouseValidation|null>(null)
 const [handle,setHandle]=useState(''),[status,setStatus]=useState<WarehouseStatus|null>(null),[results,setResults]=useState<WarehouseResults|null>(null)
 const [busy,setBusy]=useState(''),[error,setError]=useState(''),[blocked,setBlocked]=useState(false),[locked,setLocked]=useState(false),[sent,setSent]=useState(false)
 const gate=useRef(false),generation=useRef(0),controller=useRef<AbortController|null>(null),identity=useRef('')
 useEffect(()=>{
  const invalidate=()=>{generation.current++;controller.current?.abort()}
  const revoke=()=>{invalidate();setBlocked(true);setResults(null);setValidation(null);setStatus(null);setSql('');setError('Session or access changed. Sign in again before continuing.');setBusy('')}
  const storage=(event:StorageEvent)=>{if(event.key==='datarise-auth-change')revoke()}
  window.addEventListener('datarise:signed-out',revoke);window.addEventListener('storage',storage)
  return()=>{invalidate();window.removeEventListener('datarise:signed-out',revoke);window.removeEventListener('storage',storage)}
 },[])
 async function action(label:string,work:(signal:AbortSignal,valid:()=>boolean)=>Promise<void>){
  if(gate.current||blocked||readOnly)return
  gate.current=true;setBusy(label);setError('');setResults(null)
  const version=generation.current,abort=new AbortController();controller.current=abort
  const valid=()=>version===generation.current&&!abort.signal.aborted
  try{await work(abort.signal,valid)}catch(e){if(valid()){setError(e instanceof Error?e.message:'Warehouse request failed. Retain the original attempt.');if(e instanceof ApiError&&(e.status===401||e.status===403)){setBlocked(true);setStatus(null);setValidation(null);setSql('')}}}
  finally{gate.current=false;if(valid())setBusy('')}
 }
 const change=(nextModel:number,nextSql:string)=>{if(gate.current||locked||blocked)return;setModel(nextModel);setSql(nextSql);setValidation(null);setError('')}
 const validate=()=>action('Validating SQL',async(signal,valid)=>{setValidation(null);const value=await validateWarehouseQuery(workspace,model,sql,signal);if(valid())setValidation(value)})
 const admit=()=>action('Admitting query',async(signal,valid)=>{
  if(!validation||validation.budget_decision!=='ALLOWED'||handle)return
  identity.current ||= crypto.randomUUID();setLocked(true)
  try{const value=await admitWarehouseQuery(workspace,model,sql,identity.current,signal);if(valid()){setHandle(value.attempt_handle);setStatus(null)}}catch(e){if(valid()&&e instanceof ApiError&&[400,409,422].includes(e.status)){identity.current='';setLocked(false);setValidation(null)}throw e}
 })
 const update=(operation?:'execute'|'refresh'|'cancel')=>action(operation==='execute'?'Executing original query':operation==='cancel'?'Requesting cancellation':'Refreshing original query',async(signal,valid)=>{
  if(!handle)return
  if(operation==='execute'){if(sent)return;setSent(true)}
  const value=await warehouseQueryStatus(workspace,handle,operation,signal)
  if(valid()){setStatus(value);if(value.status==='ADMITTED'||value.status==='PREPARED')setSent(false)}
 })
 const resume=(original:string)=>action('Checking original query',async(signal,valid)=>{if(handle||locked)return;queryHandle(original);const value=await warehouseQueryStatus(workspace,original,undefined,signal);if(valid()){setHandle(original);setLocked(true);setSent(!['ADMITTED','PREPARED'].includes(value.status));setStatus(value)}})
 const loadResults=()=>action('Loading verified results',async(signal,valid)=>{if(!handle||status?.status!=='SUCCEEDED'||!status.results_available)return;const value=await warehouseQueryResults(workspace,handle,signal);if(valid())setResults(value)})
 const terminal=!!status&&['SUCCEEDED','FAILED','CANCELLED','EXPIRED'].includes(status.status)
 const reset=()=>{if(gate.current||blocked||(locked&&!terminal))return;identity.current='';setHandle('');setStatus(null);setResults(null);setValidation(null);setLocked(false);setSent(false);setError('')}
 return {model,sql,validation,handle,status,results,busy,error,blocked,locked,sent,terminal,change,validate,admit,update,resume,loadResults,reset}
}
