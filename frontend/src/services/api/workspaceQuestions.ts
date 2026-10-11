import { ApiError, backendId, request } from './client'

export type AnswerStatus = 'ANSWERED' | 'CLARIFICATION_REQUIRED' | 'REFRESH_REQUIRED' | 'UNSUPPORTED' | 'NO_DATA' | 'FAILED'
export interface WorkspaceAnswer {
  answer_id: number
  workspace_id: number
  question: string
  intent?: {question_type: string; time_grain?: string | null} | null
  status: AnswerStatus
  freshness: 'FRESH' | 'STALE' | 'UNAVAILABLE'
  computed_at: string
  primary_answer: string
  result_kind: 'scalar' | 'grouped' | 'comparison' | null
  value: number | string | null
  groups: { dimensions: (string | number | boolean | null)[]; value: number | string | null }[]
  comparison: { periods: [string, string][]; values: (number | string | null)[]; difference: number | string | null; percentage_change: number | null } | null
  dimensions: string[]
  provenance: { dataset_id: number; dataset_name: string; state_id: number; gold_run_id: number }[]
  relationship_versions: { relationship_id: number; version: number }[]
  interpretation: string
  explanation: string
  caveats: string[]
  follow_ups: string[]
  choices: { choice_id: number; label: string }[]
  reused: boolean
}
export interface QuestionRequest {
  question: string
  timezone?: string
  previous_answer_id?: number
  clarification_answer_id?: number
  choice_id?: number
}
export interface QuestionReadiness { workspace_id: number; ready: boolean; message: string; suggested_questions: string[] }
function path(workspace: string) { return `/workspaces/${backendId(workspace)}/questions` }
function owned<T extends { workspace_id: number }>(workspace: string, value: T): T {
  if (value.workspace_id !== backendId(workspace)) throw new ApiError('Answer belongs to another workspace.')
  return value
}
export async function askWorkspace(workspace: string, body: QuestionRequest, signal?: AbortSignal) {
  return owned(workspace, await request<WorkspaceAnswer>(path(workspace), { method: 'POST', body: JSON.stringify(body), signal }))
}
export async function getQuestionReadiness(workspace: string, signal?: AbortSignal) {
  return owned(workspace, await request<QuestionReadiness>(path(workspace) + '/readiness', { signal }))
}
export async function getWorkspaceAnswers(workspace: string, signal?: AbortSignal) {
  const result = owned(workspace, await request<{ workspace_id: number; answers: WorkspaceAnswer[] }>(path(workspace) + '/answers', { signal }))
  result.answers.forEach(answer => owned(workspace, answer))
  return result.answers
}
export async function refreshWorkspaceAnswer(workspace: string, answerId: number, signal?: AbortSignal) {
  return owned(workspace, await request<WorkspaceAnswer>(path(workspace) + `/answers/${answerId}/refresh`, { method: 'POST', signal }))
}
