import { useRef, useState } from "react"
import { saveSnapshotContext, type SnapshotCoverage, type SnapshotContext } from "../../../services/api/incremental"
import type { ProcessingContext } from "../../../services/api/rules"

export function SnapshotDelivery({ workspaceId, datasetId, context, reload, disabled }: {
  workspaceId: string; datasetId: string; context: ProcessingContext; reload: () => void; disabled: boolean
}) {
  const [coverage, setCoverage] = useState<SnapshotCoverage | "">("")
  const [effective, setEffective] = useState("")
  const [kind, setKind] = useState<SnapshotContext["delivery_kind"]>("NORMAL")
  const [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("")
  const active = useRef(false)
  if (context.load_strategy !== "SNAPSHOT") return null
  if (context.snapshot_context) return <div className="space-y-2 rounded-lg border border-slate-200 p-3 text-sm">
    <p>Snapshot coverage: {context.snapshot_context.coverage === "COMPLETE" ? "Complete" : "Partial / unknown"} · {context.snapshot_context.delivery_kind}</p>
    {context.snapshot_context.effective_at && <p>Effective snapshot time: {new Date(context.snapshot_context.effective_at).toLocaleString()}</p>}
    {context.snapshot_context.coverage === "PARTIAL" && <p>This delivery is not marked as a complete snapshot. Missing records will not be deactivated.</p>}
  </div>
  if (context.archived_at || context.application) return null
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!coverage || !confirmed || active.current || disabled) return
    if (effective && (!/(Z|[+-]\d{2}:\d{2})$/.test(effective) || Number.isNaN(Date.parse(effective)))) { setError("Enter an ISO timestamp with an explicit timezone, for example 2026-10-06T00:00:00Z."); return }
    active.current = true; setBusy(true); setError("")
    try { await saveSnapshotContext(workspaceId,datasetId,context.upload_request_id,{ coverage, effective_at: effective || null, delivery_kind: kind }); reload() }
    catch { setError("Declaration could not be saved. Check coverage and effective time, then refresh delivery status before retrying.") }
    finally { active.current = false; setBusy(false) }
  }
  const field = "min-h-11 w-full rounded-lg border border-slate-300 px-3"
  return <form onSubmit={save} className="min-w-0 space-y-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
    <h4 className="font-semibold">Declare this snapshot delivery</h4>
    <p>These metadata are pinned to this delivery. Verify them before saving; they cannot be edited afterward.</p>
    <label className="block space-y-1"><span>Snapshot coverage</span><select required className={field} disabled={busy || disabled} value={coverage} onChange={e => { setCoverage(e.target.value as SnapshotCoverage); setConfirmed(false) }}><option value="">Choose coverage</option><option value="PARTIAL">Partial / unknown snapshot</option>{context.load_policy?.snapshot_coverage === "COMPLETE" && <option value="COMPLETE">Complete snapshot</option>}</select></label>
    <p>{coverage === "COMPLETE" ? "Records missing from this delivery may be marked inactive." : "This delivery is not marked as a complete snapshot. Missing records will not be deactivated."}</p>
    <label className="block space-y-1"><span>Effective snapshot time (ISO timestamp with explicit timezone)</span><input className={field} disabled={busy || disabled} required={!!context.load_policy?.event_time_column || kind !== "NORMAL"} placeholder="2026-10-06T00:00:00Z" value={effective} onChange={e => { setEffective(e.target.value); setConfirmed(false) }} /></label>
    <p>Use the source's declared business time. DataRise will not infer it from the filename or arrival time. Keep timing consistent across this dataset.</p>
    <label className="block space-y-1"><span>Delivery context</span><select className={field} disabled={busy || disabled} value={kind} onChange={e => { setKind(e.target.value as SnapshotContext["delivery_kind"]); setConfirmed(false) }}><option value="NORMAL">Normal delivery</option><option value="CORRECTION">Correction</option><option value="BACKFILL">Backfill</option></select></label>
    {kind !== "NORMAL" && <p>Corrections and backfills preserve missing records. Older effective periods cannot replace newer trusted state. Corrections may change values at the current effective period.</p>}
    <label className="flex min-h-11 items-start gap-2"><input type="checkbox" required disabled={busy || disabled} checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /><span>I confirm this delivery's coverage and effective time.</span></label>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    <button className="min-h-11 rounded-lg bg-indigo-600 px-4 text-white disabled:opacity-50" disabled={busy || disabled || !coverage || !confirmed}>{busy ? "Saving…" : "Save snapshot declaration"}</button>
  </form>
}
