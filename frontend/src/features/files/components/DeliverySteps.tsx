import type { UploadState } from "../uploadWorkflow"
import { uploadStep } from "../uploadStep"
export function DeliverySteps({ state, configuring, inspected, inspecting }: { state: UploadState; configuring: boolean; inspected: boolean; inspecting: boolean }) {
  const current = uploadStep(state, configuring, inspected, inspecting)
  return <ol aria-label="Delivery steps" className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
    {["Choose File", "Inspect", "Check Compatibility", "Configure Contract", "Upload", "Ready to Process"].map((step, index) => <li key={step} aria-current={current === index ? "step" : undefined} className={`rounded-lg border p-2 ${current === index ? "border-indigo-300 bg-indigo-50 font-semibold text-indigo-900" : "border-slate-200 text-slate-500"}`}>{index + 1}. {step}{index === 3 ? " (if needed)" : ""}</li>)}
  </ol>
}
