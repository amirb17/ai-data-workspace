import { useCallback } from "react"
import { Link, useParams, useSearchParams } from "react-router-dom"
import { useProcessingContext } from "../../../features/ingestion/useProcessingContext"
import { RuleReviewForm } from "../../../features/ingestion/components/RuleReviewForm"
import { datasetProcessing, type ProcessingContext } from "../../../services/api/rules"
import { useApiResource } from "../../../services/api/useApiResource"
import { ApiFeedback } from "../../../components/ui/ApiFeedback"
import { StatusBadge } from "../../../components/ui/StatusBadge"

export function RuleSituation({ context, reviewing, reviewHref }: { context: ProcessingContext; reviewing: boolean; reviewHref: string }) {
  return <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
    <StatusBadge status={context.rule_state === "FINALIZED" ? "FINALIZED" : context.status === "AWAITING_RULES" ? "AWAITING_RULES" : "DRAFT"} />
    {context.rule_state === "FINALIZED" ? <><h3 className="font-semibold">Approved Rule Version {context.rule_version}{context.rules_reused ? " reused" : ""}</h3><p className="text-sm text-slate-600">Used by compatible deliveries. No action required.</p></> : context.status === "AWAITING_RULES" ? <><h3 className="font-semibold">Rules need your review</h3><p className="text-sm text-slate-600">Bronze profiling found questions that affect validation. Your answers may be reused by future compatible deliveries.</p>{!reviewing && <Link className="inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-white" to={reviewHref}>Review Rules</Link>}</> : <p className="text-sm text-slate-600">Process this delivery in Processing to profile its source before reviewing rules.</p>}
  </section>
}
function DeliveryRules({ workspaceId, datasetId, uploadId, reviewing }: { workspaceId: string; datasetId: string; uploadId: number; reviewing: boolean }) {
  const { context, error, reload } = useProcessingContext(workspaceId, datasetId, uploadId)
  if (!context) return <ApiFeedback error={error} retry={reload} loading="Loading delivery rules…" />
  return <div className="space-y-4">
    <RuleSituation context={context} reviewing={reviewing} reviewHref={`/app/workspaces/${workspaceId}/datasets/${datasetId}/rules?upload=${uploadId}`} />
    {reviewing && context.status === "AWAITING_RULES" && context.dataset_version_file_id && <RuleReviewForm key={context.dataset_version_file_id} workspaceId={workspaceId} datasetId={datasetId} associationId={context.dataset_version_file_id} onChanged={reload} />}
  </div>
}
export function DatasetRulesPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const [params, setParams] = useSearchParams()
  const load = useCallback((signal: AbortSignal) => datasetProcessing(workspaceId, datasetId, false, signal), [workspaceId, datasetId])
  const resource = useApiResource(`rule-deliveries:${workspaceId}:${datasetId}`, load)
  const deliveries = resource.data?.deliveries ?? []
  const requested = params.get("upload")
  const selected = requested ? deliveries.find(d => String(d.context.upload_request_id) === requested) : deliveries.find(d => d.context.status === "AWAITING_RULES") ?? deliveries.at(-1)
  return <div className="space-y-5">
    <div><h2 className="text-xl font-semibold">Processing Rules</h2><p className="mt-1 text-sm text-slate-600">Approved validation decisions are reused by compatible deliveries.</p></div>
    {!resource.data ? <ApiFeedback error={resource.error} retry={resource.retry} loading="Loading deliveries…" /> : !deliveries.length ? <section className="space-y-2 rounded-xl border border-slate-200 bg-white p-5"><h3 className="font-semibold">No rules to review yet</h3><p className="text-sm text-slate-600">Add a delivery in Files, then start processing to discover validation questions.</p></section> : <>
      <label className="block space-y-2 text-sm"><span>Source delivery</span><select className="min-h-11 w-full rounded-lg border bg-white p-2" value={selected?.context.upload_request_id ?? ""} onChange={event => setParams({upload:event.target.value})}>
        {!selected && <option value="">Select a delivery in this dataset</option>}
        {deliveries.map(d => <option key={d.context.upload_request_id} value={d.context.upload_request_id}>{d.source_file_name}</option>)}
      </select></label>
      {selected ? <DeliveryRules key={`${workspaceId}/${datasetId}/${selected.context.upload_request_id}`} workspaceId={workspaceId} datasetId={datasetId} uploadId={selected.context.upload_request_id} reviewing={!!requested} /> : <p role="alert">The selected delivery is not in this dataset.</p>}
    </>}
    <Link className="inline-flex min-h-11 items-center font-medium text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/processing`}>Go to Processing</Link>
  </div>
}
