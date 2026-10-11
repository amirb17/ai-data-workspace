import { useEffect, useRef, useState } from 'react'
import { askWorkspace, getQuestionReadiness, getWorkspaceAnswers, refreshWorkspaceAnswer } from '../../../services/api/workspaceQuestions'
import type { QuestionReadiness, WorkspaceAnswer } from '../../../services/api/workspaceQuestions'
import { AnswerCard } from './AnswerCard'

export function WorkspaceAsk({ workspaceId }: { workspaceId: string }) {
  const [question, setQuestion] = useState('')
  const [answers, setAnswers] = useState<WorkspaceAnswer[]>([])
  const [readiness, setReadiness] = useState<QuestionReadiness | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [context, setContext] = useState<number | undefined>()
  const controller = useRef<AbortController | null>(null)
  const active = useRef(false)
  const revision = useRef(0)
  useEffect(() => {
    const scope = new AbortController(); controller.current = scope
    let reading = false
    async function load() {
      if (reading || active.current) return
      reading = true
      const startedRevision = revision.current
      try {
        const [ready, history] = await Promise.all([getQuestionReadiness(workspaceId, scope.signal), getWorkspaceAnswers(workspaceId, scope.signal)])
        if (!scope.signal.aborted && startedRevision === revision.current) { setReadiness(ready); setAnswers(history) }
      } catch { if (!scope.signal.aborted && startedRevision === revision.current) setError('Could not load workspace questions. Reload to retry.') }
      finally { reading = false }
    }
    void load()
    const timer = setInterval(() => void load(), 15000)
    return () => { scope.abort(); clearInterval(timer) }
  }, [workspaceId])
  async function run(text: string, refreshId?: number, clarification?: { answer: number; choice: number }) {
    if (active.current || (!text.trim() && !refreshId)) return
    revision.current += 1
    active.current = true; setBusy(true); setError('')
    const signal = controller.current?.signal
    try {
      const answer = refreshId ? await refreshWorkspaceAnswer(workspaceId, refreshId, signal) : await askWorkspace(workspaceId, {
        question: text, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, previous_answer_id: context,
        clarification_answer_id: clarification?.answer, choice_id: clarification?.choice,
      }, signal)
      if (!signal?.aborted) {
        setAnswers(old => [answer, ...old.filter(item => item.answer_id !== answer.answer_id)].slice(0, 20))
        setQuestion(''); setContext(answer.status === 'ANSWERED' ? answer.answer_id : undefined)
      }
    } catch { if (!signal?.aborted) setError('Could not answer. Reload to check saved answers, or retry.') }
    finally { active.current = false; if (!signal?.aborted) setBusy(false) }
  }
  return <section className="min-w-0 space-y-4 rounded-xl border bg-slate-50 p-4 sm:p-5" aria-labelledby="ask-workspace-title">
    <header className="flex flex-wrap justify-between gap-3"><div><h2 id="ask-workspace-title" className="text-xl font-semibold">Ask your workspace</h2><p className="text-sm text-slate-600">Grounded answers from current trusted analytics.</p></div><button type="button" disabled={busy} onClick={() => { setContext(undefined); setQuestion('') }} className="min-h-11 rounded-lg border bg-white px-3 text-sm">New analysis</button></header>
    {readiness && <p className="text-sm text-slate-600">{readiness.message}</p>}
    {context && <p className="text-xs text-slate-500">Following up on the previous answer. Choose New analysis to reset the context.</p>}
    <form onSubmit={event => { event.preventDefault(); void run(question) }} className="space-y-2"><label htmlFor="workspace-question" className="block text-sm font-medium">Your question</label><textarea id="workspace-question" value={question} onChange={event => setQuestion(event.target.value)} maxLength={1000} rows={2} disabled={busy} placeholder="How many orders are there?" className="w-full min-w-0 resize-y rounded-lg border bg-white p-3 focus:ring-2 focus:ring-indigo-500" /><button disabled={busy || !question.trim()} className="min-h-11 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white disabled:opacity-50">{busy ? 'Preparing your answer…' : 'Ask workspace'}</button></form>
    {busy && <p role="status" className="text-sm">Preparing a safe plan and checking current analytics…</p>}
    {error && <p role="alert" className="break-words text-sm text-red-700">{error}</p>}
    <div className="flex flex-wrap gap-2">{readiness?.suggested_questions.map(text => <button key={text} disabled={busy} onClick={() => { setContext(undefined); setQuestion(text) }} className="min-h-11 max-w-full break-words rounded-lg border bg-white px-3 text-left text-sm">{text}</button>)}</div>
    {answers.length > 0 && <h3 className="text-sm font-semibold">Recent answers</h3>}
    {answers.map(answer => <AnswerCard key={answer.answer_id} answer={answer} busy={busy} refresh={() => void run(answer.question, answer.answer_id)} choose={choice => void run(answer.question, undefined, { answer: answer.answer_id, choice })} followUp={text => { setContext(answer.answer_id); setQuestion(text) }} />)}
  </section>
}
