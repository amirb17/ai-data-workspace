import {useCallback, useState} from 'react'
import {Database, GitBranch, RefreshCw} from 'lucide-react'
import {ApiFeedback} from '../../../components/ui/ApiFeedback'
import {getWarehouseCatalog, getWarehouseRelationships, type WarehouseCatalog, type WarehouseModel, type WarehouseRelationship} from '../../../services/api/warehouseCatalog'
import {useApiResource} from '../../../services/api/useApiResource'
import {WarehouseGraph} from './WarehouseGraph'
import {currentWarehouseRelationships} from './warehouseGraphView'

export function WarehouseModelPage({workspace}:{workspace:string}) {
  const load=useCallback(async(signal:AbortSignal)=>{
    const [catalog,relationships]=await Promise.all([getWarehouseCatalog(workspace,signal),getWarehouseRelationships(workspace,signal)])
    return {catalog,relationships}
  },[workspace])
  const {data,error,retry}=useApiResource(`warehouse-model:${workspace}`,load)
  return data
    ? <WarehouseModelContent key={workspace} catalog={data.catalog} relationships={data.relationships} reload={retry}/>
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

export function WarehouseModelContent({catalog,relationships=[],reload}:{catalog:WarehouseCatalog;relationships?:WarehouseRelationship[];reload:()=>void}) {
  const [selectedId,setSelectedId]=useState<number|null>(null)
  const [selectedRelationId,setSelectedRelationId]=useState<number|null>(null)
  const [search,setSearch]=useState('')
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
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
      <GitBranch className="mr-2 inline-block" size={16}/>
      {verified.length ? `${verified.length} explicitly reviewed relationship(s) available below.` : 'No current reviewed FACT-to-DIMENSION relationships. Tables are grouped by type, not connected by inferred keys.'} Warehouse SQL remains disabled.
    </div>
    <section className="rounded-xl border border-slate-200 bg-white p-4" aria-labelledby="warehouse-readiness-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="warehouse-readiness-title" className="text-base font-semibold text-slate-900">Warehouse release readiness</h2>
        <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-900">Review required</span>
      </div>
      <p className="mt-2 text-sm text-slate-600">Approved model definitions are available for inspection. BigQuery warehouse SQL remains disabled until independent security, serving and execution checks are accepted.</p>
      <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-700">
        {catalog.readiness.blockers.map(item=><li key={item.code}>{item.label}</li>)}
      </ul>
    </section>
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
      <p className="text-xs text-amber-800">Resource {selected.resource_registered?'registered':'not registered'} · Serving {selected.serving_status.toLowerCase().replaceAll('_',' ')} · Not queryable</p>
      <div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b"><th className="py-2 pr-3">Column</th><th className="py-2 pr-3">Type</th><th className="py-2">Role</th></tr></thead><tbody>{selected.columns.map(column=><tr key={column.name} className="border-b last:border-b-0"><td className="break-all py-2 pr-3">{column.name}</td><td className="py-2 pr-3">{column.type}</td><td className="py-2">{column.role}</td></tr>)}</tbody></table></div>
      <h3 className="font-semibold">Source datasets</h3>
      <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">{selected.sources.map(source=><li key={source.dataset_id}>{source.dataset_name} ({source.purpose.toLowerCase()})</li>)}</ul>
    </section>}
  </section>
}
