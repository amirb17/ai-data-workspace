import {ResponsiveContainer,BarChart,Bar,LineChart,Line,XAxis,YAxis,Tooltip,CartesianGrid} from 'recharts'
import type {SqlExecution} from '../../../services/api/sqlAnalytics'
import {chartCompatible} from './sqlChartModel'

export function SqlChart({result,x,y,kind}: {result:SqlExecution;x:string;y:string;kind:string}) {
  const xi=Number(x),yi=Number(y),columns=result.columns||[]
  const compatible=chartCompatible(result,x,y,kind),line=kind==='line'
  if(!compatible||kind==='table'||kind==='line'&&!line)return <p className="text-sm text-slate-500">Choose compatible fields with unique X values. Charts show up to 100 returned rows. Exact decimals and large integers stay in Results. Line charts require at least two date/time points.</p>
  const data=result.rows.map(r=>({label:String(r[xi]),value:r[yi],time:Date.parse(String(r[xi]))}))
  if(line)data.sort((a,b)=>a.time-b.time)
  return <div className="min-w-0"><p className="mb-2 text-xs text-slate-500">Returned rows only · no additional aggregation or interpolation</p><div role="img" aria-label={`${kind} chart: ${columns[yi].name} by ${columns[xi].name}`} className="h-64 w-full"><ResponsiveContainer width="100%" height="100%">{line?<LineChart data={data}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="label" tick={{fontSize:11}}/><YAxis tick={{fontSize:11}}/><Tooltip/><Line dataKey="value" name={columns[yi].name} stroke="#4f46e5" type="linear" connectNulls={false}/></LineChart>:<BarChart data={data}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="label" tick={{fontSize:11}}/><YAxis tick={{fontSize:11}}/><Tooltip/><Bar dataKey="value" name={columns[yi].name} fill="#4f46e5"/></BarChart>}</ResponsiveContainer></div></div>
}
