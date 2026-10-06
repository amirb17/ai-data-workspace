import { useCallback, useState } from "react"
import { getIncrementalFoundation, saveLoadPolicy, type LoadStrategy, type IncrementalFoundation } from "../../../../services/api/incremental"
import { useApiResource } from "../../../../services/api/useApiResource"
import { ApiFeedback } from "../../../../components/ui/ApiFeedback"
import { Button } from "../../../../components/ui/Button"
import type { DatasetContract } from "../types"
import { ApplicationMetrics } from "../../../ingestion/components/ApplicationMetrics"

const loadDescriptions: Record<LoadStrategy, string> = {
  APPEND: "Adds new records to the trusted dataset without modifying existing records.",
  UPSERT: "Insert new records and update matching records using a business key.",
  SNAPSHOT: "Each delivery represents the complete current source state.",
}
export function IncrementalPolicy({ workspaceId, datasetId, browserContract, summaryOnly = false }: {
  workspaceId: string; datasetId: string; browserContract?: DatasetContract; summaryOnly?: boolean
}) {
  const load = useCallback((signal: AbortSignal) => getIncrementalFoundation(workspaceId, datasetId, signal), [workspaceId,datasetId])
  const resource = useApiResource(`incremental:${workspaceId}:${datasetId}`, load)
  return <section className="min-w-0 space-y-3 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
    <h3 className="font-semibold">Incremental dataset policy</h3>
    <p className="text-sm text-slate-600">These settings are saved on the backend. APPEND updates the trusted dataset after Silver validation. UPSERT and SNAPSHOT execution are not available yet; Gold remains delivery-level output.</p>
    {resource.data ? <PolicyContent key={`${workspaceId}:${datasetId}:${resource.data.policies.at(-1)?.policy_id ?? 0}`} data={resource.data} workspaceId={workspaceId} datasetId={datasetId} browserContract={browserContract} summaryOnly={summaryOnly} refresh={resource.retry} />
      : <ApiFeedback loading="Loading incremental settings…" error={resource.error} retry={resource.retry} />}
  </section>
}
function PolicyContent({ data, workspaceId, datasetId, browserContract, summaryOnly, refresh }: {
  data: IncrementalFoundation; workspaceId: string; datasetId: string; browserContract?: DatasetContract; summaryOnly: boolean; refresh: () => void
}) {
  const latest = data.policies.at(-1)
  const [editing, setEditing] = useState(false), [saving, setSaving] = useState(false)
  const [version, setVersion] = useState(latest ? String(latest.dataset_version_id) : "")
  const [strategy, setStrategy] = useState<LoadStrategy | "">(latest?.load_strategy ?? "")
  const [keys, setKeys] = useState(latest?.business_keys.join(", ") ?? "")
  const [evolution, setEvolution] = useState<"STRICT" | "ALLOW_ADDITIVE" | "">(latest?.schema_evolution_policy ?? "")
  const [eventTime, setEventTime] = useState(latest?.event_time_column ?? "")
  const [confirmed, setConfirmed] = useState(false), [error, setError] = useState("")
  const schema = data.schema_versions.find(v => String(v.dataset_version_id) === version)
  const lastApplied = data.applications.slice().reverse().find(a => a.status === "SUCCESS")
  const pending = data.applications.filter(a => !a.archived_at && a.status === "PREPARED").length
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!strategy || !evolution || !schema || !confirmed || saving) return
    setSaving(true); setError("")
    try {
      await saveLoadPolicy(workspaceId, datasetId, { dataset_version_id: schema.dataset_version_id, expected_policy_version: latest?.policy_version ?? 0,
        load_strategy: strategy, business_keys: keys.trim() ? keys.split(",").map(k => k.trim()) : [], schema_evolution_policy: evolution,
        event_time_column: eventTime || null, confirm_policy_change: confirmed })
      setEditing(false); refresh()
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Unable to save policy.") }
    finally { setSaving(false) }
  }
  const field = "min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm"
  return <>
    <dl className="grid gap-3 text-sm sm:grid-cols-2">
      <div><dt className="text-slate-500">Load strategy</dt><dd>{latest ? `${latest.load_strategy} · Policy ${latest.policy_version}` : "Not configured"}</dd></div>
      <div><dt className="text-slate-500">Business key (in order)</dt><dd className="break-words">{latest?.business_keys.join(" + ") || "—"}</dd></div>
      <div><dt className="text-slate-500">Current trusted records</dt><dd>{data.current_state?.row_count.toLocaleString() ?? "—"}</dd></div>
      <div><dt className="text-slate-500">Latest applied delivery</dt><dd>{lastApplied ? `Delivery ${lastApplied.upload_request_id}` : "—"}</dd></div>
      <div><dt className="text-slate-500">Prepared applications</dt><dd>{pending}</dd></div>
      <div><dt className="text-slate-500">Last incremental change</dt><dd>{lastApplied ? `${lastApplied.inserted_rows ?? "—"} inserted · ${lastApplied.updated_rows ?? "—"} updated` : "—"}</dd></div>
    </dl>
    {latest && <p className="text-sm text-slate-600">{loadDescriptions[latest.load_strategy]}</p>}
    {latest?.load_strategy === "APPEND" && <p className="text-sm text-slate-600">{latest.business_keys.length ? "Event / record key: " + latest.business_keys.join(" + ") + ". Used to identify records that have already been received." : "DataRise will prevent the same delivery from being applied twice, but cannot identify the same business record across different deliveries without a key."}</p>}
    {lastApplied && <p className="break-words text-sm">Latest applied delivery: {lastApplied.source_file_name ?? `Delivery ${lastApplied.upload_request_id}`} · Added {lastApplied.inserted_rows ?? "—"} · Duplicates ignored {lastApplied.duplicate_rows ?? "—"} · Quarantined {lastApplied.rejected_rows ?? "—"} · Incremental conflicts {lastApplied.incremental_rejected_rows ?? "—"}</p>}
    {data.current_state && <p className="text-sm text-amber-900">Trusted dataset state is published. Dataset-state analytics await a rebuild; existing Gold results describe individual deliveries.</p>}
    {data.applications.at(-1) && <div className="space-y-2 border-t border-slate-200 pt-3"><h4 className="text-sm font-medium">Latest application · {data.applications.at(-1)!.status}</h4><ApplicationMetrics metrics={data.applications.at(-1)!} /></div>}
    {!summaryOnly && !editing && <Button variant="secondary" disabled={!!data.current_state} onClick={() => setEditing(true)}>{latest ? "Review policy change" : "Configure backend policy"}</Button>}
    {!summaryOnly && data.current_state && <p className="text-sm text-slate-600">Changing a policy after state publication requires an explicit migration. That flow is not available yet.</p>}
    {!summaryOnly && editing && <form onSubmit={save} className="space-y-4 border-t border-slate-200 pt-4">
      {browserContract && <div className="space-y-2 text-sm"><p>Browser inspection settings are suggestions only. Review them and explicitly save a backend policy.</p><Button variant="secondary" type="button" disabled={saving} onClick={() => { setStrategy(browserContract.loadMode); setKeys(browserContract.primaryKey.join(", ")); setEvolution(browserContract.schemaEvolutionPolicy); setConfirmed(false) }}>Review browser settings</Button></div>}
      <label className="block space-y-1 text-sm"><span>Schema version</span><select required disabled={saving} className={field} value={version} onChange={e => { setVersion(e.target.value); setEventTime(""); setConfirmed(false) }}><option value="">Choose an inspected schema</option>{data.schema_versions.filter(v => v.columns.length).map(v => <option key={v.dataset_version_id} value={v.dataset_version_id}>Schema {v.version_number}</option>)}</select></label>
      {!data.schema_versions.some(v => v.columns.length) && <p className="text-sm text-slate-600">Inspect a delivery through Processing first to establish an authoritative schema.</p>}
      <label className="block space-y-1 text-sm"><span>Load strategy</span><select required disabled={saving} className={field} value={strategy} onChange={e => { setStrategy(e.target.value as LoadStrategy); setConfirmed(false) }}><option value="">Choose a strategy</option>{Object.keys(loadDescriptions).map(mode => <option key={mode}>{mode}</option>)}</select></label>
      {strategy && <p className="text-sm text-slate-600">{loadDescriptions[strategy]}{strategy === "SNAPSHOT" && " Future missing records will be marked inactive rather than deleted."}</p>}
      <label className="block space-y-1 text-sm"><span>Business key columns (ordered, comma-separated)</span><input className={field} disabled={saving} required={strategy === "UPSERT" || strategy === "SNAPSHOT"} value={keys} onChange={e => { setKeys(e.target.value); setConfirmed(false) }} /></label>
      {schema && <p className="break-words text-sm text-slate-600">Available columns: {schema.columns.map(c => c.name).join(", ")}</p>}
      <label className="block space-y-1 text-sm"><span>Schema changes</span><select required disabled={saving} className={field} value={evolution} onChange={e => { setEvolution(e.target.value as "STRICT" | "ALLOW_ADDITIVE"); setConfirmed(false) }}><option value="">Choose a policy</option><option value="STRICT">Exact structure required</option><option value="ALLOW_ADDITIVE">Allow extra columns during inspection</option></select></label>
      <p className="text-sm text-slate-600">A different schema still requires an explicit state migration before incremental application.</p>
      <label className="block space-y-1 text-sm"><span>Event time column (optional)</span><select disabled={saving} className={field} value={eventTime} onChange={e => { setEventTime(e.target.value); setConfirmed(false) }}><option value="">Not configured</option>{schema?.columns.filter(c => ["DATE","DATETIME"].includes(c.data_type)).map(c => <option key={c.name}>{c.name}</option>)}</select></label>
      <label className="flex min-h-11 items-start gap-2 text-sm"><input type="checkbox" disabled={saving} required checked={confirmed} onChange={e => setConfirmed(e.target.checked)} className="mt-1" /><span>I confirm these explicit settings{latest ? " for a new prospective policy version. Historical applications keep their original policy" : " for this dataset"}.</span></label>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex flex-wrap gap-2"><Button type="submit" disabled={saving || !confirmed || !schema || !strategy || !evolution}>{saving ? "Saving…" : "Save backend policy"}</Button><Button variant="secondary" type="button" disabled={saving} onClick={() => setEditing(false)}>Cancel</Button><Button variant="ghost" type="button" disabled={saving} onClick={refresh}>Refresh settings</Button></div>
    </form>}
  </>
}
