import {useMemo,useState} from 'react'
import type {SqlCatalog} from '../../../services/api/sqlAnalytics'
import {sqlInsightSuggestions} from './sqlInsightSuggestions'

const categories=['Business insights','Customer insights','Explore data'] as const
export function SqlSuggestions({catalog,busy,onOpen}:{
  catalog:SqlCatalog
  busy:boolean
  onOpen:(sql:string)=>void
}){
  const [filter,setFilter]=useState<'All'|(typeof categories)[number]>('All')
  const [expanded,setExpanded]=useState(false)
  const ideas=useMemo(()=>sqlInsightSuggestions(catalog),[catalog])
  const filtered=ideas.filter(idea=>filter==='All'||idea.category===filter)
  const visible=expanded?filtered:filtered.slice(0,6)
  if(!ideas.length)return null
  return <section aria-labelledby="sql-ideas-title" className="space-y-3 rounded-xl border border-indigo-100 bg-white p-4 shadow-sm sm:p-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h3 id="sql-ideas-title" className="font-semibold text-slate-900">Business insights &amp; query ideas</h3>
        <p className="mt-1 text-sm text-slate-600">Explore trusted data with ready-to-edit SQL. Suggestions remain available after running a query.</p>
      </div>
      <span className="rounded-full bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700">{ideas.length} ideas</span>
    </div>
    <div className="flex flex-wrap gap-2" aria-label="Filter query ideas">
      {(['All',...categories] as const).map(category=><button key={category} type="button"
        aria-pressed={filter===category} onClick={()=>{setFilter(category);setExpanded(false)}}
        className={`min-h-11 rounded-lg border px-3 text-sm ${filter===category?'border-indigo-500 bg-indigo-50 font-medium text-indigo-800':'border-slate-200 text-slate-700 hover:bg-slate-50'}`}>{category}</button>)}
    </div>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {visible.map(idea=><button key={idea.id} type="button" disabled={busy}
        onClick={()=>onOpen(idea.sql)}
        className="min-h-28 rounded-xl border border-slate-200 bg-white p-4 text-left transition-colors hover:border-indigo-300 hover:bg-indigo-50/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 disabled:opacity-50">
        <span className="text-xs font-medium text-indigo-700">{idea.category}</span>
        <span className="mt-1 block text-sm font-semibold text-slate-900">{idea.title}</span>
        <span className="mt-1 block text-xs leading-5 text-slate-600">{idea.description}</span>
      </button>)}
    </div>
    {filtered.length>6&&<button type="button" className="min-h-11 text-sm font-medium text-indigo-700 hover:underline"
      onClick={()=>setExpanded(value=>!value)}>{expanded?'Show fewer ideas':`Show all ${filtered.length} ideas`}</button>}
    <p className="text-xs text-slate-500">Suggestions use only current queryable columns and confirmed relationships. Check filters and business definitions before treating output as an approved metric.</p>
  </section>
}
