import type { UploadState } from "./uploadWorkflow"
export function uploadStep(state: UploadState, configuring: boolean, inspected: boolean, inspecting: boolean) {
  if (state === "SUCCESS") return 5
  if (["INITIATING", "UPLOADING", "COMPLETING", "FAILED"].includes(state)) return 4
  if (configuring) return 3
  if (inspected) return 2
  return inspecting ? 1 : 0
}
