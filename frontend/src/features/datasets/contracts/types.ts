export type DatasetColumnContract = {
  name: string
  dataType:
    | "STRING"
    | "INTEGER"
    | "DECIMAL"
    | "DATE"
    | "BOOLEAN"
  required: boolean
}

export type DatasetLoadMode =
  | "APPEND"
  | "UPSERT"
  | "SNAPSHOT"

export type DatasetContract = {
  datasetId: number
  datasetName: string

  columns: DatasetColumnContract[]

  primaryKey: string[]

  loadMode: DatasetLoadMode
}