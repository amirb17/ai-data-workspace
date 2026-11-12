import {useCallback, useState} from 'react'
import {Database, GitBranch, RefreshCw} from 'lucide-react'
import {ApiFeedback} from '../../../components/ui/ApiFeedback'
import {getWarehouseCatalog, getWarehouseRelationships, type WarehouseCatalog, type WarehouseModel, type WarehouseRelationship} from '../../../services/api/warehouseCatalog'
import {useApiResource} from '../../../services/api/useApiResource'
import {WarehouseGraph} from './WarehouseGraph'
import {WarehouseResultReadiness} from './WarehouseResultReadiness'
import {currentWarehouseRelationships} from './warehouseGraphView'
import {warehouseModelInspectionJson} from './warehouseModelInspection'

export function WarehouseModelPage({workspace}:{workspace:string}) {
  const load=useCallback(async(signal:AbortSignal)=>{
    const [catalog,relationships]=await Promise.all([getWarehouseCatalog(workspace,signal),getWarehouseRelationships(workspace,signal)])
    return {catalog,relationships}
  },[workspace])
  const {data,error,retry}=useApiResource(`warehouse-model:${workspace}`,load)
  return data
    ? <WarehouseModelContent key={workspace} workspace={workspace} catalog={data.catalog} relationships={data.relationships} reload={retry}/>
    : <ApiFeedback loading="Loading approved warehouse models…" error={error} retry={retry}/>
}

function ModelCard({model,selected,onSelect}:{model:WarehouseModel;selected:boolean;onSelect:()=>void}) {
  return <button type="button" onClick={onSelect} aria-pressed={selected}
    className={`min-w-0 rounded-xl border bg-white p-4 text-left shadow-sm transition-colors hover:border-indigo-400 ${selected?'border-indigo-600 ring-2 ring-indigo-100':'border-slate-200'}`}>
    <div className="flex items-center justify-between gap-3">
      <strong className="break-all text-sm text-slate-900">{model.name}</strong>
      <span className={`rounded px-2 py-1 text-xs font-semibold ${model.model_type==='FACT'?'bg-indigo-100 text-indigo-800':'bg-slate-100 text-slate-700'}`}>{model.model_type}</span>
    </div>
    <p className="mt-2 text-xs text-slate-600">Version {model.version_number} · Approved definition</p>
    <p className="mt-2 text-xs text-slate-600">{model.model_type==='FACT'?model.grain||'Grain unavailable':model.scd_type?.replaceAll('_',' ')||'Dimension'}</p>
    <p className="mt-2 text-xs text-amber-800">Serving not verified · SQL unavailable</p>
  </button>
}

export function WarehouseModelContent({workspace,catalog,relationships=[],reload}:{workspace?:string;catalog:WarehouseCatalog;relationships?:WarehouseRelationship[];reload:()=>void}) {
  const [selectedId,setSelectedId]=useState<number|null>(null)
  const [selectedRelationId,setSelectedRelationId]=useState<number|null>(null)
  const [search,setSearch]=useState('')
  const [copyStatus,setCopyStatus]=useState('')
  const models=catalog.models.filter(model=>model.name.toLowerCase().includes(search.trim().toLowerCase()))
  const facts=models.filter(model=>model.model_type==='FACT')
  const dimensions=models.filter(model=>model.model_type==='DIMENSION')
  const selected=catalog.models.find(model=>model.model_id===selectedId)
  const current=new Map(catalog.models.map(model=>[model.model_id,model]))
  const verified=currentWarehouseRelationships(catalog.workspace_id,models,relationships)
  const selectedRelation=verified.find(relation=>relation.relationship_id===selectedRelationId)
  return <section className="min-w-0 space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Warehouse models</h1>
        <p className="mt-2 text-sm text-slate-600">Explore approved FACT and DIMENSION definitions and their source datasets.</p>
        <p className="mt-2 text-xs text-slate-600">{catalog.models.length} approved models · {catalog.models.filter(m=>m.model_type==='FACT').length} facts · {catalog.models.filter(m=>m.model_type==='DIMENSION').length} dimensions</p>
      </div>
      <button type="button" onClick={reload} className="inline-flex min-h-11 items-center gap-2 rounded-lg border bg-white px-3 text-sm"><RefreshCw size={16}/>Check for updates</button>
    </header>
    <section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="Star schema relationship overview">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold text-slate-900"><GitBranch size={18}/>Star schema · FACT to DIMENSION</h2>
        <span className="text-xs text-slate-600">{verified.length} reviewed connection{verified.length===1?'':'s'}</span>
      </div>
      <p className="mt-2 text-sm text-slate-600">Facts record events at a declared grain. Dimensions describe customers, products, dates and other business context. A reviewed foreign-key-to-business-key mapping connects many fact rows to one dimension member; historical SCD2 joins also require an event-time rule.</p>
      {verified.length>0?<div className="mt-3 grid gap-3 lg:grid-cols-2">
        {verified.map(relation=><button type="button" key={relation.relationship_id}
          onClick={()=>{setSelectedRelationId(relation.relationship_id);setSelectedId(null)}}
          className="rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-left hover:border-indigo-500">
          <span className="text-xs font-semibold uppercase tracking-wide text-indigo-700">Reviewed · many-to-one</span>
          <strong className="mt-1 block break-words text-sm">{current.get(relation.fact_model_id)?.name} (N) → {current.get(relation.dimension_model_id)?.name} (1)</strong>
          <span className="mt-1 block break-all font-mono text-xs text-slate-700">{relation.key_pairs.map(pair=>pair.fact_column+' = '+pair.dimension_column).join(' · ')}</span>
          <span className="mt-1 block text-xs text-slate-600">{relation.temporal_policy==='EVENT_TIME_AS_OF'?'Historical as-of / SCD2':'Current dimension member'}</span>
        </button>)}
      </div>:<p role="status" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">No current reviewed FACT-to-DIMENSION keys have been registered. The graph shows tables as separate nodes; it must not draw guessed joins merely because column names match. Review source-key uniqueness and relationship contracts before registering an edge.</p>}
      <p className="mt-3 text-xs text-slate-500">Relationships are logical metadata. Physical tables are published separately in BigQuery; model approval does not guarantee a queryable serving table.</p>
    </section>
    <section className="rounded-xl border border-slate-200 bg-white p-4" aria-labelledby="warehouse-readiness-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="warehouse-readiness-title" className="text-base font-semibold text-slate-900">Warehouse release readiness</h2>
        <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-900">Review required</span>
      </div>
      <p className="mt-2 text-sm text-slate-600">Approved model definitions are available for inspection. Query execution requires separately verified BigQuery serving tables, authenticated runtime and controlled result authorization.</p>
      <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-700">
        {catalog.readiness.blockers.map(item=><li key={item.code}>{item.label}</li>)}
      </ul>
    </section>
    {workspace&&<WarehouseResultReadiness workspace={workspace}/>}
    <label className="block text-sm text-slate-700">Search warehouse tables
      <input className="mt-1 block min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Search approved models…" />
    </label>
    {!catalog.models.length&&<p className="rounded-xl border bg-white p-5 text-sm text-slate-600">No approved warehouse models are available in this workspace. Approved FACT and DIMENSION models will appear here after their configurations and source membership are verified.</p>}
    {!!catalog.models.length&&!models.length&&<p role="status" className="text-sm text-slate-600">No warehouse models match your search.</p>}
    {!!models.length&&<div className="grid min-w-0 gap-4 lg:grid-cols-2">
      <section className="min-w-0 space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4" aria-label="Fact models">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><Database size={17}/>FACT tables ({facts.length})</h2>
        <div className="grid gap-3">{facts.map(model=><ModelCard key={model.model_id} model={model} selected={model.model_id===selectedId} onSelect={()=>setSelectedId(model.model_id)}/>)}</div>
        {!facts.length&&<p className="text-sm text-slate-500">No approved facts</p>}
      </section>
      <section className="min-w-0 space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4" aria-label="Dimension models">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><Database size={17}/>DIMENSION tables ({dimensions.length})</h2>
        <div className="grid gap-3">{dimensions.map(model=><ModelCard key={model.model_id} model={model} selected={model.model_id===selectedId} onSelect={()=>setSelectedId(model.model_id)}/>)}</div>
        {!dimensions.length&&<p className="text-sm text-slate-500">No approved dimensions</p>}
      </section>
    </div>}
    {!!models.length&&<WarehouseGraph models={models} relationships={verified} selected={selectedId}
      onSelect={id=>{setSelectedId(id);setSelectedRelationId(null)}}
      onInspectRelationship={id=>{setSelectedRelationId(id);setSelectedId(null)}}/>}
    {selectedRelation&&<section aria-label="Selected warehouse relationship" className="rounded-xl border border-indigo-300 bg-white p-4 text-sm">
      <div className="flex justify-between gap-2"><strong>{selectedRelation.name}</strong>
        <button type="button" className="min-h-11 rounded-lg border px-3" onClick={()=>setSelectedRelationId(null)}>Close relationship</button>
      </div>
      <p className="mt-2">FACT {current.get(selectedRelation.fact_model_id)?.name} → DIMENSION {current.get(selectedRelation.dimension_model_id)?.name}</p>
      <p className="mt-1 text-slate-600">{selectedRelation.key_pairs.map(pair=>pair.fact_column+' = '+pair.dimension_column).join(', ')}</p>
      <p className="mt-1 text-slate-600">{selectedRelation.temporal_policy==='EVENT_TIME_AS_OF'?'SCD2 historical as-of join':'Current-member join'} · Approved versions only</p>
    </section>}
    {!!verified.length&&<section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="Reviewed warehouse relationships">
      <h2 className="text-base font-semibold text-slate-900">Reviewed FACT-to-DIMENSION relationships ({verified.length})</h2>
      <ul className="mt-3 space-y-3">{verified.map(relation=><li key={relation.relationship_id} className="rounded-lg border border-slate-200 p-3 text-sm">
        <strong className="block text-slate-900">{current.get(relation.fact_model_id)?.name} → {current.get(relation.dimension_model_id)?.name}</strong>
        <span className="mt-1 block text-slate-600">{relation.name} · Many-to-one · {relation.temporal_policy==='EVENT_TIME_AS_OF'?'Historical as-of':'Current member'}</span>
        <span className="mt-1 block text-xs text-slate-600">Join keys: {relation.key_pairs.map(pair=>pair.fact_column+' = '+pair.dimension_column).join(', ')}</span>
        {relation.temporal_policy==='EVENT_TIME_AS_OF'&&<span className="mt-1 block text-xs text-slate-600">Fact event time: {relation.fact_event_time_column}</span>}
      </li>)}</ul>
    </section>}
    {selected&&<section aria-label="Warehouse table details" className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between gap-2"><h2 className="break-all text-lg font-semibold">{selected.name}</h2><button onClick={()=>setSelectedId(null)} className="min-h-11 rounded-lg border px-3 text-sm">Close details</button></div>
      <p className="text-sm text-slate-600">{selected.model_type} · Version {selected.version_number} · {selected.scd_type??selected.grain??'Approved definition'}</p>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="min-h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium"
          onClick={()=>{
            const content=warehouseModelInspectionJson(selected, verified)
            if(!navigator.clipboard?.writeText){setCopyStatus('Clipboard unavailable; model inspection could not be copied.');return}
            void navigator.clipboard.writeText(content).then(
              ()=>setCopyStatus('Approved model definition copied for inspection only; not executable SQL.'),
              ()=>setCopyStatus('Clipboard unavailable; model inspection could not be copied.'),
            )
          }}>Copy model definition</button>
        {copyStatus&&<span role="status" className="text-xs text-slate-600">{copyStatus}</span>}
      </div>
      <p className="text-xs text-amber-800">Resource {selected.resource_registered?'registered':'not registered'} · Serving {selected.serving_status.toLowerCase().replaceAll('_',' ')} · Not queryable</p>
      <div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b"><th className="py-2 pr-3">Column</th><th className="py-2 pr-3">Type</th><th className="py-2">Role</th></tr></thead><tbody>{selected.columns.map(column=><tr key={column.name} className="border-b last:border-b-0"><td className="break-all py-2 pr-3">{column.name}</td><td className="py-2 pr-3">{column.type}</td><td className="py-2">{column.role}</td></tr>)}</tbody></table></div>
      <h3 className="font-semibold">Source datasets</h3>
      <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">{selected.sources.map(source=><li key={source.dataset_id}>{source.dataset_name} ({source.purpose.toLowerCase()})</li>)}</ul>
    </section>}
  </section>
}
