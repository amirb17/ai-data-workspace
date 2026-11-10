import {useCallback, useState} from 'react'
import {Database, GitBranch, RefreshCw} from 'lucide-react'
import {ApiFeedback} from '../../../components/ui/ApiFeedback'
import {getWarehouseCatalog, type WarehouseCatalog, type WarehouseModel} from '../../../services/api/warehouseCatalog'
import {useApiResource} from '../../../services/api/useApiResource'

export function WarehouseModelPage({workspace}:{workspace:string}) {
  const load=useCallback((signal:AbortSignal)=>getWarehouseCatalog(workspace,signal),[workspace])
  const {data,error,retry}=useApiResource(`warehouse-model:${workspace}`,load)
  return data
    ? <WarehouseModelContent key={workspace} catalog={data} reload={retry}/>
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

export function WarehouseModelContent({catalog,reload}:{catalog:WarehouseCatalog;reload:()=>void}) {
  const [selectedId,setSelectedId]=useState<number|null>(null)
  const [search,setSearch]=useState('')
  const models=catalog.models.filter(model=>model.name.toLowerCase().includes(search.trim().toLowerCase()))
  const facts=models.filter(model=>model.model_type==='FACT')
  const dimensions=models.filter(model=>model.model_type==='DIMENSION')
  const selected=catalog.models.find(model=>model.model_id===selectedId)
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
      FACT-to-DIMENSION connections are not modeled yet. Tables below are grouped by type, not connected by inferred keys. Warehouse SQL remains disabled.
    </div>
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
