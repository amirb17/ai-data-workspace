export const columnDataTypes = ["STRING", "INTEGER", "DECIMAL", "BOOLEAN", "DATE", "DATETIME"] as const
export const datasetLoadModes = ["APPEND", "UPSERT", "SNAPSHOT"] as const
export const schemaEvolutionPolicies = ["STRICT", "ALLOW_ADDITIVE"] as const
export type DatasetColumnContract = {
  name: string
  dataType: typeof columnDataTypes[number]
  required: boolean
}
export type DatasetLoadMode = typeof datasetLoadModes[number]
export type SchemaEvolutionPolicy = typeof schemaEvolutionPolicies[number]
export type DatasetContract = {
  workspaceId: number
  datasetId: number
  datasetName: string
  columns: DatasetColumnContract[]
  primaryKey: string[]
  loadMode: DatasetLoadMode
  schemaEvolutionPolicy: SchemaEvolutionPolicy
}
