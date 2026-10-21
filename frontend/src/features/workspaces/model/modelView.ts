import type { DatasetListItem } from '../../datasets/types'
import type { DatasetProfile, ColumnEvidence } from '../../../services/api/profile'
import type { Understanding } from '../../../services/api/understanding'
import type { RelationshipsView, RelationshipEvidence } from '../../../services/api/relationships'

export interface ModelDataset {
  dataset: DatasetListItem
  profile: Pick<DatasetProfile,'freshness'|'profile_ready'|'profile_id'|'current_state_id'|'state_version'|'profile_version'|'profile_timestamp'> & {
    summary: Pick<NonNullable<DatasetProfile['summary']>,'row_count'|'column_count'|'business_key'|'event_time_column'> | null
    columns: Pick<ColumnEvidence,'original_name'|'canonical_type'|'authoritative_business_key'|'null_ratio'|'distinct_count'|'sensitivity_hints'>[]
  }
  meaning: Pick<Understanding,'status'|'source_profile_id'|'source_state_id'> & {
    suggestion: {reasoning: {
      entity: {primary: {label:string|null;rationale:string}}
      columns: Pick<NonNullable<Understanding['suggestion']>['reasoning']['columns'][number],'column_name'|'business_meaning'|'suggested_role'|'warnings'>[]
    }} | null
  }
}
export interface ModelRelationship {
  id: string
  evidence: RelationshipEvidence
  suggested: boolean
  reviewedAt?: string
}
export function currentProfile(value: ModelDataset) {
  return value.dataset.status === 'ACTIVE' && value.profile.freshness === 'READY' && value.profile.profile_ready
}
export function currentMeaning(value: ModelDataset) {
  return currentProfile(value) && value.meaning.status === 'READY'
    && value.meaning.source_profile_id === value.profile.profile_id
    && value.meaning.source_state_id === value.profile.current_state_id
}
export function description(value: ModelDataset) {
  if (value.dataset.description.trim()) return value.dataset.description
  if (!currentMeaning(value)) return 'Description not available yet'
  const entity = value.meaning.suggestion?.reasoning.entity.primary
  return entity?.label && entity.rationale ? entity.rationale : 'Description not available yet'
}
export function modelRelationships(view: RelationshipsView, datasets: ModelDataset[]): ModelRelationship[] {
  const byId=new Map(datasets.map(d=>[d.dataset.id,d]))
  const included = (e: RelationshipEvidence) => e.workspace_id === view.workspace_id && [e.parent.dataset_id,e.child.dataset_id].every(id=>{
    const d=byId.get(id), pin=e.source_pins.find(p=>p.dataset_id===id)
    return d && currentProfile(d) && pin && pin.state_version===d.profile.state_version && pin.profile_version===d.profile.profile_version
  })
  const confirmed = view.reviewed_relationships.filter(r => r.status === 'CONFIRMED' && r.structural_status === 'CURRENT' && r.verification_status === 'CURRENT' && included(r.evidence))
    .map(r => ({ id: `confirmed:${r.candidate_id}`, evidence: r.evidence, suggested: false, reviewedAt: r.reviewed_at }))
  const keys = new Set(confirmed.map(r => r.evidence.candidate_key))
  return [...confirmed, ...(view.status === 'READY' ? view.candidates.filter(c => c.review_status === 'REVIEW_REQUIRED' && included(c.evidence) && !keys.has(c.evidence.candidate_key))
    .map(c => ({ id: `suggested:${c.candidate_id}`, evidence: c.evidence, suggested: true })) : [])]
}
export function relationshipsNeedRefresh(view: RelationshipsView, visible: ModelRelationship[]) {
  const currentIds = new Set(visible.filter(r => !r.suggested).map(r => r.id))
  return view.status === 'STALE' || view.status === 'FAILED'
    || view.reviewed_relationships.some(r => r.status === 'CONFIRMED' && !currentIds.has(`confirmed:${r.candidate_id}`))
}
export function relationshipText(e: RelationshipEvidence) {
  if (e.candidate_cardinality === 'ONE_TO_ONE') return `One record in ${e.parent.dataset_name} matches one record in ${e.child.dataset_name}`
  if (e.candidate_cardinality === 'ONE_TO_MANY') return `One record in ${e.parent.dataset_name} can match many records in ${e.child.dataset_name}`
  if (e.candidate_cardinality === 'MANY_TO_ONE') return `Many records in ${e.parent.dataset_name} can match one record in ${e.child.dataset_name}`
  return 'Multiplicity needs review'
}
// Stable topological levels; cyclic/unconnected nodes retain a stable final level.
export function layoutDatasets(ids: number[], relationships: ModelRelationship[]) {
  const sorted = [...ids].sort((a,b) => a-b), levels = new Map<number, number>()
  const edges = relationships.filter(r => !r.suggested).map(r => [r.evidence.parent.dataset_id,r.evidence.child.dataset_id])
  let pending = [...sorted]
  for (let pass = 0; pending.length && pass <= ids.length; pass++) {
    const ready = pending.filter(id => edges.filter(([,child]) => child === id).every(([parent]) => levels.has(parent)))
    if (!ready.length) break
    for (const id of ready) levels.set(id, Math.max(0, ...edges.filter(([,child]) => child === id).map(([parent]) => levels.get(parent)!+1)))
    pending = pending.filter(id => !ready.includes(id))
  }
  const rows = new Map<number,number>()
  return sorted.map(id => {
    const level = levels.get(id) ?? 0, row = rows.get(level) ?? 0
    rows.set(level,row+1)
    return {id, position:{x:level*350,y:row*235}}
  })
}
