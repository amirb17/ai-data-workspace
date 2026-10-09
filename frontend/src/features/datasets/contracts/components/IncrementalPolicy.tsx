import { useCallback, useState } from "react"
import { getIncrementalFoundation, saveLoadPolicy, type LoadStrategy, type IncrementalFoundation, type SnapshotCoverage } from "../../../../services/api/incremental"
import { useApiResource } from "../../../../services/api/useApiResource"
import { ApiFeedback } from "../../../../components/ui/ApiFeedback"
import { Button } from "../../../../components/ui/Button"
import type { DatasetContract } from "../types"
import { ApplicationMetrics } from "../../../ingestion/components/ApplicationMetrics"
import { businessKeyError } from "../policyValidation"

const loadDescriptions: Record<LoadStrategy, string> = {
  APPEND: "Adds new records to the trusted dataset without modifying existing records.",
  UPSERT: "New records are inserted. Existing records with the same key are updated when their values change.",
  SNAPSHOT: "Compares a keyed delivery with trusted state. Only explicitly complete snapshots may mark missing records inactive.",
}
export function IncrementalPolicy({ workspaceId, datasetId, browserContract, summaryOnly = false }: {
  workspaceId: string; datasetId: string; browserContract?: DatasetContract; summaryOnly?: boolean
}) {
  const load = useCallback((signal: AbortSignal) => getIncrementalFoundation(workspaceId, datasetId, signal), [workspaceId,datasetId])
  const resource = useApiResource(`incremental:${workspaceId}:${datasetId}`, load)
  return <section className="min-w-0 space-y-3 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
    <h3 className="font-semibold">Incremental dataset policy</h3>
    <p className="text-sm text-slate-600">These settings are saved on the backend. APPEND updates the trusted dataset after Silver validation; UPSERT can insert and update records. SNAPSHOT preserves record history with explicit coverage. Refresh Analytics separately to build cumulative results from trusted state.</p>
    {resource.data ? <PolicyContent key={`${workspaceId}:${datasetId}:${resource.data.policies.at(-1)?.policy_id ?? 0}`} data={resource.data} workspaceId={workspaceId} datasetId={datasetId} browserContract={browserContract} summaryOnly={summaryOnly} refresh={resource.retry} />
      : <ApiFeedback loading="Loading incremental settings…" error={resource.error} retry={resource.retry} />}
  </section>
}
function PolicyContent({ data, workspaceId, datasetId, browserContract, summaryOnly, refresh }: {
  data: IncrementalFoundation; workspaceId: string; datasetId: string; browserContract?: DatasetContract; summaryOnly: boolean; refresh: () => void
}) {
  const latest = data.policies.find(p => p.policy_id === data.current_state?.policy_id) ?? data.policies.at(-1)
  const [editing, setEditing] = useState(false), [saving, setSaving] = useState(false)
  const [version, setVersion] = useState(latest ? String(latest.dataset_version_id) : "")
  const [strategy, setStrategy] = useState<LoadStrategy | "">(latest?.load_strategy ?? "")
  const [keys, setKeys] = useState(latest?.business_keys.join(", ") ?? "")
  const [evolution, setEvolution] = useState<"STRICT" | "ALLOW_ADDITIVE" | "">(latest?.schema_evolution_policy ?? "")
  const [eventTime, setEventTime] = useState(latest?.event_time_column ?? "")
  const [coverage, setCoverage] = useState<SnapshotCoverage | "">(latest?.snapshot_coverage ?? "")
  const [confirmed, setConfirmed] = useState(false), [error, setError] = useState("")
  const schema = data.schema_versions.find(v => String(v.dataset_version_id) === version)
  const keyError = businessKeyError(strategy,keys,schema?.columns.map(c => c.name) ?? [])
  const lastApplied = data.applications.find(a => data.current_state != null && a.status === "SUCCESS" && a.result_state_id === data.current_state.state_id) ?? data.applications.slice().reverse().find(a => a.status === "SUCCESS")
  const pending = data.applications.filter(a => !a.archived_at && a.status === "PREPARED").length
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!strategy || !evolution || !schema || !confirmed || saving || keyError || (strategy === "SNAPSHOT" && !coverage)) return
    setSaving(true); setError("")
    try {
      await saveLoadPolicy(workspaceId, datasetId, { dataset_version_id: schema.dataset_version_id, expected_policy_version: latest?.policy_version ?? 0,
        load_strategy: strategy, business_keys: keys.trim() ? keys.split(",").map(k => k.trim()) : [], schema_evolution_policy: evolution,
        event_time_column: eventTime || null, confirm_policy_change: confirmed, snapshot_coverage: strategy === "SNAPSHOT" ? coverage as SnapshotCoverage : null })
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
      <div><dt className="text-slate-500">Last incremental change</dt><dd>{lastApplied ? `${lastApplied.inserted_rows ?? "—"} inserted · ${lastApplied.updated_rows ?? "—"} updated · ${lastApplied.unchanged_rows ?? "—"} unchanged` : "—"}</dd></div>
      {latest?.load_strategy === "UPSERT" && <div><dt className="text-slate-500">Change ordering</dt><dd>{latest.event_time_column || "Delivery order"}</dd></div>}
      {latest?.load_strategy === "SNAPSHOT" && <><div><dt className="text-slate-500">Snapshot coverage</dt><dd>{latest.snapshot_coverage === "COMPLETE" ? "Complete snapshot" : "Partial / unknown snapshot"}</dd></div><div><dt className="text-slate-500">Active records</dt><dd>{data.current_state?.active_rows?.toLocaleString() ?? "—"}</dd></div><div><dt className="text-slate-500">Inactive records</dt><dd>{data.current_state?.inactive_rows?.toLocaleString() ?? "—"}</dd></div><div><dt className="text-slate-500">Latest trusted snapshot</dt><dd>{data.current_state?.snapshot_boundary_at ? new Date(data.current_state.snapshot_boundary_at).toLocaleString() : "Business time not configured"}</dd></div></>}
    </dl>
    {latest && <p className="text-sm text-slate-600">{loadDescriptions[latest.load_strategy]}</p>}
    {latest?.load_strategy === "SNAPSHOT" && <><p className="text-sm text-slate-600">{latest.snapshot_coverage === "COMPLETE" ? "Records missing from this delivery may be marked inactive." : "Missing records will remain active."} Each delivery requires an explicit declaration in Processing. A complete policy permits a delivery to be declared partial.</p><p className="text-sm text-slate-600">{latest.event_time_column ? `Record ordering: ${latest.event_time_column}. Declare an effective timestamp for each snapshot.` : "With effective timestamps, older snapshots preserve newer state. Without business time, snapshots use application order. Keep timing consistent across this dataset."}</p>{lastApplied && <p className="text-sm">Latest changes: {lastApplied.deactivated_rows ?? "—"} deactivated · {lastApplied.reactivated_rows ?? "—"} reactivated</p>}</>}
    {latest?.load_strategy === "APPEND" && <p className="text-sm text-slate-600">{latest.business_keys.length ? "Event / record key: " + latest.business_keys.join(" + ") + ". Used to identify records that have already been received." : "DataRise will prevent the same delivery from being applied twice, but cannot identify the same business record across different deliveries without a key."}</p>}
    {latest?.load_strategy === "UPSERT" && <><p className="text-sm text-slate-600">These columns identify one logical record in this dataset: {latest.business_keys.join(" + ")}.</p><p className="text-sm text-slate-600">{latest.event_time_column ? "DataRise uses this timestamp to prevent older updates from overwriting newer records. Older late-arriving records will not overwrite newer data." : "Updates are applied in delivery order because no change-ordering column is configured. Latest applied delivery wins for current-state UPSERT."}</p></>}
    {lastApplied && <p className="break-words text-sm">Latest applied delivery: {lastApplied.source_file_name ?? `Delivery ${lastApplied.upload_request_id}`} · Added {lastApplied.inserted_rows ?? "—"} · Duplicates ignored {lastApplied.duplicate_rows ?? "—"} · Quarantined {lastApplied.rejected_rows ?? "—"} · Incremental conflicts {lastApplied.incremental_rejected_rows ?? "—"}</p>}
    {data.current_state?.state_analytics_status === "STALE" && <p className="text-sm text-amber-900">Analytics refresh required. Trusted dataset state is published. Refresh Analytics from Overview or Analytics to rebuild cumulative results.</p>}
    {data.applications.at(-1) && <div className="space-y-2 border-t border-slate-200 pt-3"><h4 className="text-sm font-medium">Latest application · {data.applications.at(-1)!.status}</h4><ApplicationMetrics metrics={data.applications.at(-1)!} /></div>}
    {!summaryOnly && !editing && <Button variant="secondary" disabled={!!data.current_state} onClick={() => setEditing(true)}>{latest ? "Review policy change" : "Configure backend policy"}</Button>}
    {!summaryOnly && data.current_state && <p className="text-sm text-slate-600">Changing a policy after state publication requires an explicit migration. That flow is not available yet.</p>}
    {!summaryOnly && editing && <form onSubmit={save} className="space-y-4 border-t border-slate-200 pt-4">
      {browserContract && <div className="space-y-2 text-sm"><p>Browser inspection settings are suggestions only. Review them and explicitly save a backend policy.</p><Button variant="secondary" type="button" disabled={saving} onClick={() => { setStrategy(browserContract.loadMode); setKeys(browserContract.primaryKey.join(", ")); setEvolution(browserContract.schemaEvolutionPolicy); setConfirmed(false) }}>Review browser settings</Button></div>}
      <label className="block space-y-1 text-sm"><span>Schema version</span><select required disabled={saving} className={field} value={version} onChange={e => { setVersion(e.target.value); setEventTime(""); setConfirmed(false) }}><option value="">Choose an inspected schema</option>{data.schema_versions.filter(v => v.columns.length).map(v => <option key={v.dataset_version_id} value={v.dataset_version_id}>Schema {v.version_number}</option>)}</select></label>
      {!data.schema_versions.some(v => v.columns.length) && <p className="text-sm text-slate-600">Inspect a delivery through Processing first to establish an authoritative schema.</p>}
      <label className="block space-y-1 text-sm"><span>Load strategy</span><select required disabled={saving} className={field} value={strategy} onChange={e => { setStrategy(e.target.value as LoadStrategy); setConfirmed(false) }}><option value="">Choose a strategy</option>{Object.keys(loadDescriptions).map(mode => <option key={mode}>{mode}</option>)}</select></label>
      {strategy && <p className="text-sm text-slate-600">{loadDescriptions[strategy]}</p>}
      {strategy === "SNAPSHOT" && <><label className="block space-y-1 text-sm"><span>Snapshot coverage</span><select required disabled={saving} className={field} value={coverage} onChange={e => { setCoverage(e.target.value as SnapshotCoverage); setConfirmed(false) }}><option value="">Choose coverage explicitly</option><option value="COMPLETE">Complete snapshot</option><option value="PARTIAL">Partial / unknown snapshot</option></select></label><p className="text-sm text-slate-600">{coverage === "COMPLETE" ? "Records missing from this delivery may be marked inactive." : "Missing records will remain active."} Rejected rows withhold deactivation. Corrections and backfills preserve missing records.</p></>}
      <label className="block space-y-1 text-sm"><span>Business key columns (ordered, comma-separated)</span><input className={field} disabled={saving} required={strategy === "UPSERT" || strategy === "SNAPSHOT"} value={keys} onChange={e => { setKeys(e.target.value); setConfirmed(false) }} /></label>
      <p className="text-sm text-slate-600">These columns identify one logical record in this dataset. For example: customer_id, or order_id + line_number (enter composite keys separated by commas).</p>
      {keyError && <p role="alert" className="text-sm text-amber-900">{keyError}</p>}
      {schema && <p className="break-words text-sm text-slate-600">Available columns: {schema.columns.map(c => c.name).join(", ")}</p>}
      <label className="block space-y-1 text-sm"><span>Schema changes</span><select required disabled={saving} className={field} value={evolution} onChange={e => { setEvolution(e.target.value as "STRICT" | "ALLOW_ADDITIVE"); setConfirmed(false) }}><option value="">Choose a policy</option><option value="STRICT">Exact structure required</option><option value="ALLOW_ADDITIVE">Allow extra columns during inspection</option></select></label>
      <p className="text-sm text-slate-600">A different schema still requires an explicit state migration before incremental application.</p>
      <label className="block space-y-1 text-sm"><span>Change ordering column (optional)</span><select disabled={saving} className={field} value={eventTime} onChange={e => { setEventTime(e.target.value); setConfirmed(false) }}><option value="">Not configured</option>{(schema?.event_time_columns ?? schema?.columns.filter(c => ["DATE","DATETIME"].includes(c.data_type)).map(c => c.name) ?? []).map(name => <option key={name}>{name}</option>)}</select></label>
      {strategy === "UPSERT" && <p className="text-sm text-slate-600">{eventTime ? "Older updates will not overwrite newer records. Timestamp values must include an explicit timezone; DataRise will not guess one." : "Updates are applied in delivery order because no change-ordering column is configured."}</p>}
      <label className="flex min-h-11 items-start gap-2 text-sm"><input type="checkbox" disabled={saving} required checked={confirmed} onChange={e => setConfirmed(e.target.checked)} className="mt-1" /><span>I confirm these explicit settings{latest ? " for a new prospective policy version. Historical applications keep their original policy" : " for this dataset"}.</span></label>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex flex-wrap gap-2"><Button type="submit" disabled={saving || !confirmed || !schema || !strategy || !evolution || !!keyError || (strategy === "SNAPSHOT" && !coverage)}>{saving ? "Saving…" : "Save backend policy"}</Button><Button variant="secondary" type="button" disabled={saving} onClick={() => setEditing(false)}>Cancel</Button><Button variant="ghost" type="button" disabled={saving} onClick={refresh}>Refresh settings</Button></div>
    </form>}
  </>
}
