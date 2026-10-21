import { useState } from 'react'
import { currentMeaning, currentProfile, type ModelDataset, type ModelRelationship } from './modelView'

const roleLabels:Record<string,string>={IDENTIFIER:'Key',ENTITY_REFERENCE:'Reference',MEASURE:'Measure',DIMENSION:'Category',CATEGORY:'Category',STATUS:'Category',TIME_DIMENSION:'Time'}

export function ModelColumns({value,relationships}: {value:ModelDataset;relationships:ModelRelationship[]}) {
  const [search,setSearch]=useState('')
  const columns=value.profile.columns.filter(c=>c.original_name.toLowerCase().includes(search.toLowerCase()))
  if(!currentProfile(value))return <p>Prepare a current profile to inspect columns.</p>
  return <div className="space-y-4">
    <label className="block text-sm">Search columns<input className="mt-2 w-full rounded-lg border p-3" value={search} onChange={e=>setSearch(e.target.value)}/></label>
    <p className="text-xs text-slate-500">Meanings and roles are current suggestions, not approved definitions.</p>
    {!columns.length&&<p role="status" className="text-sm">No columns match your search.</p>}
    <div className="overflow-x-auto"><table className="w-full text-left text-sm">
      <thead><tr>{['Column','Type','Meaning','Key / role'].map(h=><th className="p-2" key={h} scope="col">{h}</th>)}</tr></thead>
      <tbody>{columns.map(column=>{
        const meaning=currentMeaning(value)?value.meaning.suggestion?.reasoning.columns.find(m=>m.column_name===column.original_name):null
        const reference=relationships.some(r=>!r.suggested&&r.evidence.child.dataset_id===value.dataset.id&&r.evidence.child.columns.includes(column.original_name))
        const badges=[column.authoritative_business_key?'PK':null,reference?'FK':null,meaning?roleLabels[meaning.suggested_role]:null].filter(Boolean)
        return <tr key={column.original_name} className="border-t align-top">
          <td className="max-w-40 break-words p-2"><details>
            <summary className="min-h-11 cursor-pointer py-2 font-medium">{column.original_name}</summary>
            <p className="text-xs">Nulls: {column.null_ratio===null?'Unavailable':`${(column.null_ratio*100).toLocaleString(undefined,{maximumFractionDigits:2})}%`} · Distinct: {column.distinct_count.toLocaleString()}</p>
            {column.sensitivity_hints.length>0&&<p className="mt-2 text-xs text-amber-800">Potentially sensitive</p>}
          </details></td>
          <td className="p-2">{column.canonical_type}</td>
          <td className="max-w-48 break-words p-2">{meaning?.business_meaning||'Meaning not available yet'}{meaning?.warnings.length?<p className="mt-2 text-xs text-amber-800">Needs review</p>:null}</td>
          <td className="p-2">{badges.join(' · ')||'—'}</td>
        </tr>
      })}</tbody>
    </table></div>
  </div>
}
