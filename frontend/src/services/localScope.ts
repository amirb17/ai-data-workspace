let prefix = ""
export function configureBackendScope(apiBase: string, userId: number) {
  prefix = `datarise-backend-${encodeURIComponent(apiBase)}-user-${userId}-`
}
export function hasBackendScope() { return prefix !== "" }
export function scopedStorageKey(workspaceId: string, datasetId: string, kind: string) {
  return `${prefix || "datarise-"}workspace-${workspaceId}-dataset-${datasetId}-${kind}`
}
