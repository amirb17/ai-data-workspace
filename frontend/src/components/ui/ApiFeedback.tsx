import { Button } from "./Button"
export function ApiFeedback({ error, retry }: { error?: string; retry: () => void }) {
  return <div role={error ? "alert" : "status"} className="space-y-3 rounded-xl border border-slate-200 bg-white p-6">
    <p className="text-sm text-slate-700">{error ?? "Loading from backend…"}</p>
    {error && <Button onClick={retry}>Retry</Button>}
  </div>
}
