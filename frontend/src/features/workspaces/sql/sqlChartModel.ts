import type {SqlExecution} from '../../../services/api/sqlAnalytics'
import {numeric} from './sqlPresentation'
export function chartXOptions(result:SqlExecution,kind:string) {
  return (result.columns||[]).map((c,i)=>({value:String(i),label:c.name,detail:c.type})).filter(c=>kind==='line'?/^(DATE|TIMESTAMP)/.test(c.detail):!numeric(c.detail)&&!/^BOOL/i.test(c.detail)).map(c=>{
    const values=result.rows.map(r=>r[Number(c.value)])
    const reason=!values.length?'No returned rows.':values.length>100?'Charts support up to 100 returned rows. Reduce your SQL limit.':values.some(v=>v===null)?'X values must be non-null.':new Set(values.map(String)).size!==values.length?'X values must be unique. Group results in your query first.':kind==='line'&&(values.length<2||values.some(v=>!Number.isFinite(Date.parse(String(v)))))?'Line charts need at least two valid date/time points.':''
    return {...c,disabled:!!reason,reason}
  })
}
export function chartYOptions(result:SqlExecution) {
  return (result.columns||[]).map((c,i)=>({value:String(i),label:c.name,detail:c.type})).filter(c=>numeric(c.detail)&&result.rows.length>0&&result.rows.every(r=>typeof r[Number(c.value)]==='number'&&Number.isFinite(r[Number(c.value)])))
}
export function chartCompatible(result:SqlExecution,x:string,y:string,kind:string) {return kind!=='table'&&chartXOptions(result,kind).some(c=>c.value===x&&!c.disabled)&&chartYOptions(result).some(c=>c.value===y)}
