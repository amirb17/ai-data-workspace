import { useMemo } from 'react'
import { Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, useReactFlow, type NodeProps, type Node, type Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { currentProfile, description, layoutDatasets, type ModelDataset, type ModelRelationship } from './modelView'

type DatasetNode = Node<{value:ModelDataset; dimmed:boolean}, 'dataset'>
function Card({data,selected}: NodeProps<DatasetNode>) {
  const {value} = data, summary = currentProfile(value) ? value.profile.summary : null
  return <div title={value.dataset.name} className={`w-64 rounded-xl border bg-white p-4 shadow-sm ${selected?'border-indigo-600 ring-2 ring-indigo-200':'border-slate-200'} ${data.dimmed?'opacity-35':''}`}>
    <Handle type="target" position={Position.Left}/><Handle type="source" position={Position.Right}/>
    <div className="flex items-start justify-between gap-2"><strong className="break-words">{value.dataset.name}</strong><span className={`shrink-0 text-xs ${summary?'text-green-700':'text-amber-800'}`}>{value.dataset.status==='ARCHIVED'?'Archived':summary?'● Ready':'Needs preparation'}</span></div>
    <p className="mt-3 text-sm text-slate-600">{summary?`${summary.row_count.toLocaleString()} rows`:'Row count unavailable'}</p>
    <p className="mt-1 truncate text-xs text-slate-600">{summary?.business_key.length?`Key · ${summary.business_key.join(', ')}`:'Key not configured'}</p>
    <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-600">{description(value)}</p>
  </div>
}
const nodeTypes = {dataset:Card}
export interface ModelGraphProps {datasets:ModelDataset[]; relationships:ModelRelationship[]; selected:number|null; select:(id:number)=>void; selectRelationship:(r:ModelRelationship)=>void; search:string; layoutRelationships?:ModelRelationship[]}
function Graph(props: ModelGraphProps) {
  const {datasets,relationships,selected,select,selectRelationship,search} = props
  const flow = useReactFlow()
  const connected = new Set(relationships.filter(r=>r.evidence.parent.dataset_id===selected || r.evidence.child.dataset_id===selected).flatMap(r=>[r.evidence.parent.dataset_id,r.evidence.child.dataset_id]))
  const layoutRelationships=props.layoutRelationships??relationships
  const layout = useMemo(()=>layoutDatasets(datasets.map(d=>d.dataset.id),layoutRelationships),[datasets,layoutRelationships])
  const nodes: DatasetNode[] = datasets.map(value=>({id:String(value.dataset.id),type:'dataset',position:layout.find(p=>p.id===value.dataset.id)!.position,selected:selected===value.dataset.id,ariaLabel:`Inspect ${value.dataset.name}`,data:{value,dimmed:!value.dataset.name.toLowerCase().includes(search.toLowerCase()) || (selected!==null && selected!==value.dataset.id && !connected.has(value.dataset.id))}}))
  const edges: Edge[] = relationships.map(r=>({id:r.id,source:String(r.evidence.parent.dataset_id),target:String(r.evidence.child.dataset_id),label:r.suggested?'Needs review':r.evidence.candidate_cardinality==='ONE_TO_ONE'?'1 — 1':r.evidence.candidate_cardinality==='ONE_TO_MANY'?'1 — N':r.evidence.candidate_cardinality==='MANY_TO_ONE'?'N — 1':'Multiplicity needs review',style:{stroke:r.suggested?'#b45309':'#94a3b8',strokeDasharray:r.suggested?'6 5':undefined},ariaLabel:`${r.evidence.parent.dataset_name} to ${r.evidence.child.dataset_name}, ${r.suggested?'needs review':'confirmed'}`,interactionWidth:24}))
  return <><div className="flex flex-wrap gap-2 border-b border-slate-200 p-3 text-sm"><button className="min-h-11 rounded-lg border px-3" onClick={()=>void flow.fitView({padding:.2})}>Fit view</button><button className="min-h-11 rounded-lg border px-3" disabled={selected===null} onClick={()=>void flow.fitView({nodes:[{id:String(selected)}],maxZoom:1.2,padding:.7})}>Focus selected dataset</button><button className="min-h-11 rounded-lg border px-3" onClick={()=>{select(-1);void flow.fitView({padding:.2})}}>Reset view</button></div>
    <div className="h-[580px]" aria-label="Dataset relationship graph" onKeyDownCapture={event=>{if(event.key!=='Enter' && event.key!==' ')return;const target=event.target as HTMLElement;const node=target.closest('.react-flow__node');const edge=target.closest('.react-flow__edge');if(node){event.preventDefault();event.stopPropagation();select(Number(node.getAttribute('data-id')))}else if(edge){const r=relationships.find(r=>r.id===edge.getAttribute('data-id'));if(r){event.preventDefault();event.stopPropagation();selectRelationship(r)}}}}><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={.2} maxZoom={1.7} nodesDraggable={false} nodesConnectable={false} elementsSelectable onNodeClick={(_,node)=>select(Number(node.id))} onEdgeClick={(_,edge)=>{const relation=relationships.find(r=>r.id===edge.id);if(relation)selectRelationship(relation)}} deleteKeyCode={null}><Background color="#e2e8f0" gap={24}/><Controls showInteractive={false}/></ReactFlow></div></>
}
export function ModelGraph(props: ModelGraphProps) { return <ReactFlowProvider><Graph {...props}/></ReactFlowProvider> }
