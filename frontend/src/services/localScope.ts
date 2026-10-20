let prefix = ""
export function clearBackendScope() {
  // Unmounting clears active views; retain per-user contracts/preferences so
  // signing out does not delete existing local product configuration.
  prefix = ''
}
export function configureBackendScope(apiBase: string, userId: number) {
  prefix = `datarise-backend-${encodeURIComponent(apiBase)}-user-${userId}-`
}
export function hasBackendScope() { return prefix !== "" }
export function scopedStorageKey(workspaceId: string, datasetId: string, kind: string) {
  return `${prefix || "datarise-"}workspace-${workspaceId}-dataset-${datasetId}-${kind}`
}
