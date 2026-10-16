import {useEffect,useState} from 'react'
import {buildSql} from '../../../services/api/sqlAnalytics'
import type {SqlBuilderInput,SqlCatalog} from '../../../services/api/sqlAnalytics'
import {builderReady} from './sqlBuilderModel'
export function useBuilderPreview(workspace:string,input:SqlBuilderInput,catalog:SqlCatalog|null) {
  const key=JSON.stringify(input),[state,setState]=useState({key:'',sql:'',message:''})
  const ready=builderReady(input)&&!!catalog?.datasets.find(d=>d.alias===input.dataset&&d.queryable)
  useEffect(()=>{
    if(!ready)return
    const controller=new AbortController()
    const timer=setTimeout(()=>{void buildSql(workspace,input,controller.signal).then(value=>{if(!controller.signal.aborted)setState({key,sql:value.sql_text||'',message:value.diagnostics.map(d=>d.message).join(' ')})}).catch(error=>{if(!controller.signal.aborted)setState({key,sql:'',message:error instanceof Error?error.message:'Could not generate SQL.'})})},300)
    return()=>{clearTimeout(timer);controller.abort()}
  },[workspace,key,catalog,ready,input])
  return {sql:ready&&state.key===key?state.sql:'',message:ready?(state.key===key?state.message:'Updating preview…'):'Choose a source and at least one complete selection to preview SQL.'}
}
