import type {Edge, Node} from '@xyflow/react'
import type {WarehouseModel, WarehouseRelationship} from '../../../services/api/warehouseCatalog'

export function currentWarehouseRelationships(
  workspaceId: number, models: WarehouseModel[], relationships: WarehouseRelationship[],
): WarehouseRelationship[] {
  const current=new Map(models.map(model=>[model.model_id,model]))
  return relationships.filter(relation=>{
    const fact=current.get(relation.fact_model_id)
    const dimension=current.get(relation.dimension_model_id)
    return relation.workspace_id===workspaceId
      && fact?.model_type==='FACT' && dimension?.model_type==='DIMENSION'
      && fact.model_version_id===relation.fact_model_version_id
      && dimension.model_version_id===relation.dimension_model_version_id
  })
}

export function warehouseGraphElements(models: WarehouseModel[], relations: WarehouseRelationship[]) {
  const facts=models.filter(m=>m.model_type==='FACT').sort((a,b)=>a.model_id-b.model_id)
  const dimensions=models.filter(m=>m.model_type==='DIMENSION').sort((a,b)=>a.model_id-b.model_id)
  const factPositions=new Map(facts.map((model,index)=>[model.model_id,index]))
  const dimensionPositions=new Map(dimensions.map((model,index)=>[model.model_id,index]))
  const nodes: Node[] = models.map(model=>({
    id:String(model.model_id),
    type:'warehouse',
    position:{
      x:model.model_type==='FACT'?0:440,
      y:((model.model_type==='FACT'?factPositions:dimensionPositions).get(model.model_id)??0)*175,
    },
    data:{model},
    ariaLabel:`Inspect ${model.model_type.toLowerCase()} ${model.name}`,
    draggable:false,
  }))
  const edges: Edge[] = relations.map(relation=>({
    id:`warehouse-relation:${relation.relationship_id}`,
    source:String(relation.fact_model_id),
    target:String(relation.dimension_model_id),
    label:relation.temporal_policy==='EVENT_TIME_AS_OF'?'N:1 · as-of':'N:1 · current',
    ariaLabel:`Reviewed ${relation.name}, many-to-one ${relation.temporal_policy==='EVENT_TIME_AS_OF'?'historical as-of':'current-member'} join`,
    interactionWidth:28,
    style:{stroke:'#64748b',strokeWidth:2},
  }))
  return {nodes,edges}
}
