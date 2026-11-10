import {useMemo} from 'react'
import {Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, useReactFlow, type Node, type NodeProps} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type {WarehouseModel, WarehouseRelationship} from '../../../services/api/warehouseCatalog'
import {warehouseGraphElements} from './warehouseGraphView'

type WarehouseNode = Node<{model:WarehouseModel},'warehouse'>
function WarehouseTable({data,selected}:NodeProps<WarehouseNode>) {
  const model=data.model
  return <div className={`w-64 rounded-xl border bg-white p-4 shadow-sm ${selected?'border-indigo-600 ring-2 ring-indigo-200':'border-slate-200'}`}>
    <Handle type="target" position={Position.Left} isConnectable={false}/>
    <strong className="block break-all text-sm text-slate-900">{model.name}</strong>
    <span className="mt-1 block text-xs font-semibold text-indigo-700">{model.model_type} · v{model.version_number}</span>
    <span className="mt-2 block text-xs text-slate-600">{model.model_type==='FACT'?model.grain||'Grain unavailable':model.scd_type?.replaceAll('_',' ')||'Dimension'}</span>
    <span className="mt-2 block text-xs text-amber-800">Serving not verified</span>
    <Handle type="source" position={Position.Right} isConnectable={false}/>
  </div>
}
const nodeTypes={warehouse:WarehouseTable}

function Graph({models,relationships,selected,onSelect,onInspectRelationship}:{
  models:WarehouseModel[]
  relationships:WarehouseRelationship[]
  selected:number|null
  onSelect:(id:number)=>void
  onInspectRelationship:(id:number)=>void
}) {
  const flow=useReactFlow()
  const elements=useMemo(()=>warehouseGraphElements(models,relationships),[models,relationships])
  const nodes:WarehouseNode[]=elements.nodes.map(node=>({...node,selected:Number(node.id)===selected,
    data:node.data as {model:WarehouseModel},type:'warehouse'}))
  const edges=elements.edges
  return <div className="hidden overflow-hidden rounded-xl border border-slate-200 bg-white md:block">
    <div className="flex flex-wrap gap-2 border-b p-3">
      <button type="button" className="min-h-11 rounded-lg border px-3 text-sm" onClick={()=>void flow.fitView({padding:.2})}>Fit warehouse graph</button>
      <button type="button" className="min-h-11 rounded-lg border px-3 text-sm" disabled={selected===null}
        onClick={()=>void flow.fitView({nodes:[{id:String(selected)}],maxZoom:1.2,padding:.7})}>Focus selected table</button>
    </div>
    <div className="h-[580px]" aria-label="Warehouse star schema graph"
      onKeyDownCapture={event=>{
        if(event.key!=='Enter'&&event.key!==' ')return
        const target=event.target as HTMLElement
        const node=target.closest('.react-flow__node')
        const edge=target.closest('.react-flow__edge')
        if(node){event.preventDefault();event.stopPropagation();onSelect(Number(node.getAttribute('data-id')))}
        else if(edge){
          const id=edge.getAttribute('data-id')?.replace('warehouse-relation:','')
          if(id&&/^\d+$/.test(id)){event.preventDefault();event.stopPropagation();onInspectRelationship(Number(id))}
        }
      }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView
        nodesDraggable={false} nodesConnectable={false} elementsSelectable
        onNodeClick={(_,node)=>onSelect(Number(node.id))}
        onEdgeClick={(_,edge)=>onInspectRelationship(Number(edge.id.replace('warehouse-relation:','')))}
        minZoom={.2} maxZoom={1.8} deleteKeyCode={null}>
        <Background color="#e2e8f0" gap={24}/><Controls showInteractive={false}/>
      </ReactFlow>
    </div>
  </div>
}
export function WarehouseGraph(props:Parameters<typeof Graph>[0]) {
  return <ReactFlowProvider><Graph {...props}/></ReactFlowProvider>
}
