import { useEffect, useRef, useState } from "react"
import { answersComplete, canFinalize, finalizeRuleAnswers, getRuleReview, questionKey, reviewAnswers, saveRuleAnswers, type RuleReview } from "../../../services/api/rules"

export function RuleReviewForm({ workspaceId, datasetId, associationId, onChanged }: { workspaceId: string; datasetId: string; associationId: number; onChanged: () => void }) {
  const [review, setReview] = useState<RuleReview>()
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [revision, setRevision] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [message, setMessage] = useState("")
  const active = useRef(false)
  useEffect(() => {
    const controller = new AbortController()
    getRuleReview(workspaceId, datasetId, associationId, controller.signal).then((value) => {
      if (controller.signal.aborted) return
      setReview(value); setAnswers(reviewAnswers(value)); setDirty(false)
    }).catch((caught) => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Unable to load rule questions.") })
    return () => controller.abort()
  }, [workspaceId, datasetId, associationId, revision])
  const reload = () => { setReview(undefined); setError(""); setRevision((value) => value + 1) }
  const submit = async (finalize: boolean) => {
    if (!review || active.current) return
    active.current = true; setBusy(true); setError(""); setMessage("")
    try {
      if (finalize) {
        const result = await finalizeRuleAnswers(workspaceId, datasetId, review)
        setMessage(result.finalized ? "Rules finalized. Approval does not start Silver or Gold." : result.message ?? "Rules are not ready. Review and save every answer.")
      } else {
        await saveRuleAnswers(workspaceId, datasetId, review, answers)
        setMessage("Answers saved. Review the saved choices, then explicitly finalize rules.")
      }
      reload(); onChanged()
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Unable to save rules.") }
    finally { active.current = false; setBusy(false) }
  }
  return <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-6">
    {error && <p role="alert" className="break-words text-sm text-red-700">{error}</p>}
    {message && <p role="status" className="text-sm text-indigo-700">{message}</p>}
    {!review ? <p role="status" className="text-sm text-slate-600">{error ? "Rule questions could not be loaded." : "Loading backend questions…"}</p> : <>
      <p className="text-sm text-slate-600">Dataset version #{review.dataset_version_id} · Rule version {review.rule_version} · {review.rule_state} · {review.active_rule_count} active rules</p>
      {review.rule_state === "FINALIZED" && <p className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">Rules already finalized. Saved answers below are read-only.</p>}
      {!review.questions.length && <p className="text-sm text-slate-600">No pending rule questions were generated for this source. Silver still requires at least one active backend rule.</p>}
      <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void submit(false) }}>
        {review.questions.map((q, index) => <div key={questionKey(q)} className="space-y-2 border-b border-slate-100 pb-4">
          <label htmlFor={`rule-answer-${associationId}-${index}`} className="block break-words font-medium text-slate-900">{q.question}</label>
          <p className="break-words text-xs text-slate-500">Column: {q.column_name} · {q.suggested_rule_type}</p>
          {q.reason && <p id={`rule-help-${associationId}-${index}`} className="break-words text-sm text-slate-600">{q.reason}</p>}
          <select id={`rule-answer-${associationId}-${index}`} aria-describedby={q.reason ? `rule-help-${associationId}-${index}` : undefined} className="min-h-11 w-full rounded-lg border border-slate-300 bg-white p-2 sm:max-w-sm" required disabled={busy || review.rule_state === "FINALIZED"} value={answers[questionKey(q)] ?? ""} onChange={(event) => { setAnswers({ ...answers, [questionKey(q)]: event.target.value }); setDirty(true); setMessage("") }}>
            <option value="">Choose an answer</option>
            {q.options.map((option) => <option key={option} value={option}>{option.replaceAll("_", " ")}</option>)}
          </select>
        </div>)}
        {review.status === "AWAITING_RULES" && <div className="flex flex-wrap gap-3">
          <button type="submit" className="min-h-11 rounded-lg bg-indigo-600 px-4 text-white disabled:opacity-50" disabled={busy || !answersComplete(review, answers) || !review.questions.length}>Save Answers</button>
          <button type="button" className="min-h-11 rounded-lg border border-indigo-300 px-4 text-indigo-700 disabled:opacity-50" disabled={!canFinalize(review, answers, dirty, busy)} onClick={() => void submit(true)}>Finalize / Approve Rules</button>
        </div>}
      </form>
      {review.status === "AWAITING_RULES" && <p className="text-sm text-slate-600">Save all answers before approval. At least one answer must activate a rule required by Silver; no choices are made automatically.</p>}
    </>}
    <button className="min-h-10 rounded-lg border border-slate-200 px-3 text-sm" disabled={busy} onClick={reload}>Reload Rules</button>
  </section>
}
