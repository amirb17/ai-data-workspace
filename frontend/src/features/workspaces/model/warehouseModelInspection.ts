import type {WarehouseModel, WarehouseRelationship} from '../../../services/api/warehouseCatalog'

/** Inspection-only contract export. Never a SQL compiler or provider permit. */
export function warehouseModelInspection(model: WarehouseModel, relationships: WarehouseRelationship[]) {
  const reviewed = relationships.filter(relation =>
    (relation.fact_model_id === model.model_id && relation.fact_model_version_id === model.model_version_id)
    || (relation.dimension_model_id === model.model_id && relation.dimension_model_version_id === model.model_version_id))
  return {
    kind: 'WAREHOUSE_MODEL_INSPECTION',
    execution_authorized: false,
    model_id: model.model_id,
    model_version_id: model.model_version_id,
    name: model.name,
    model_type: model.model_type,
    grain: model.grain,
    scd_type: model.scd_type,
    columns: model.columns.map(column => ({name: column.name, type: column.type, role: column.role})),
    sources: model.sources.map(source => ({dataset_id: source.dataset_id, name: source.dataset_name, purpose: source.purpose})),
    reviewed_relationships: reviewed.map(relation => ({
      id: relation.relationship_id,
      name: relation.name,
      fact_model_id: relation.fact_model_id,
      dimension_model_id: relation.dimension_model_id,
      key_pairs: relation.key_pairs,
      temporal_policy: relation.temporal_policy,
    })),
  }
}

export function warehouseModelInspectionJson(model: WarehouseModel, relationships: WarehouseRelationship[]) {
  return JSON.stringify(warehouseModelInspection(model, relationships), null, 2)
}
