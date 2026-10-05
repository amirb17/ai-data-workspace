// Run: node tests/rule-approval.cjs. Existing TypeScript/React, no new framework.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
for (const extension of ['.ts', '.tsx']) require.extensions[extension] = (module, filename) => module._compile(ts.transpileModule(
  fs.readFileSync(filename, 'utf8').replaceAll('import.meta.env', '({ VITE_API_BASE_URL: "http://localhost:8000" })'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } },
).outputText, filename)
const api = require('../src/services/api/rules.ts')
const { ApiError } = require('../src/services/api/client.ts')
const { IngestionBatchCard } = require('../src/features/ingestion/components/IngestionBatchCard.tsx')
const question = { column_name: 'id', suggested_rule_type: 'NOT_NULL', question: 'Is id required?', reason: 'Observed identifier', options: ['YES', 'NO'] }
const review = { workspace_id: 10, dataset_id: 20, dataset_version_file_id: 60, dataset_version_id: 50, status: 'AWAITING_RULES', rule_state: 'DRAFT', rule_version: 3, questions: [question], answers: [], active_rule_count: 0 }
async function main() {
  const empty = api.reviewAnswers(review)
  assert.equal(empty[api.questionKey(question)], '') // no silently selected default
  assert.equal(api.answersComplete(review, empty), false)
  const answers = { [api.questionKey(question)]: 'YES' }
  assert.equal(api.answersComplete(review, answers), true)
  assert.equal(api.answersComplete(review, { [api.questionKey(question)]: 'DECIMAL' }), false)
  assert.equal(api.canFinalize(review, answers, false, false), false) // no active rules yet
  const saved = { ...review, active_rule_count: 1, answers: [{ column_name: 'id', rule_type: 'NOT_NULL', answer: 'YES' }] }
  assert.equal(api.canFinalize(saved, answers, false, false), true)
  assert.equal(api.canFinalize(saved, answers, true, false), false)
  assert.equal(api.canFinalize(saved, answers, false, true), false)
  assert.equal(api.canFinalize({ ...saved, status: 'READY_FOR_SILVER' }, answers, false, false), false)
  const calls = []
  const context = { upload_request_id: 40, workspace_id: 10, dataset_id: 20, file_id: 30, status: 'AWAITING_RULES', dataset_version_file_id: 60, rule_version: 3 }
  global.fetch = async (url, options) => {
    calls.push({ url, ...options })
    return new Response(JSON.stringify(url.includes('suggestions') ? saved : url.includes('finalize') ? { finalized: true } : context), { status: 200 })
  }
  await api.getProcessingContext('10', '20', 40)
  await api.startBronze('10', '20', 40)
  await api.getRuleReview('10', '20', 60)
  assert.equal(calls.filter((c) => c.method === 'POST').length, 1) // reads do not approve
  await api.saveRuleAnswers('10', '20', saved, answers)
  assert.equal(calls.some((c) => c.url.includes('finalize')), false)
  assert.equal(JSON.parse(calls.at(-1).body).expected_rule_version, 3)
  assert.deepEqual(JSON.parse(calls.at(-1).body).answers, saved.answers)
  await api.finalizeRuleAnswers('10', '20', saved)
  assert.equal(JSON.parse(calls.at(-1).body).expected_rule_version, 3)
  assert.equal(calls.every((c) => c.url.endsWith('?workspace_id=10&dataset_id=20')), true)
  assert.equal(api.reviewAnswers(await api.getRuleReview('10', '20', 60))[api.questionKey(question)], 'YES')
  assert.throws(() => api.saveRuleAnswers('10', '20', review, empty), /Answer each/)
  for (const mismatch of [{ workspace_id: 11 }, { dataset_id: 21 }, { upload_request_id: 41 }]) {
    global.fetch = async () => new Response(JSON.stringify({ ...context, ...mismatch }))
    await assert.rejects(api.getProcessingContext('10', '20', 40), /another|mismatch/)
  }
  global.fetch = async () => new Response(JSON.stringify({ detail: 'private internal path' }), { status: 409 })
  await assert.rejects(api.finalizeRuleAnswers('10', '20', saved), (e) => e instanceof ApiError && /Reload/.test(e.message) && !e.message.includes('private'))
  const batch = { id: 'upload-40', uploadRequestId: 40, sourceFileId: 30, sourceFileName: 'a.csv', status: 'READY_TO_PROCESS', rowCount: 1, columnCount: 1, createdAt: '2026-10-05T00:00:00Z', validRows: null, rejectedRows: null, duplicateRows: null, updatedRows: null }
  const html = renderToStaticMarkup(React.createElement(IngestionBatchCard, { batch, processingStatus: 'AWAITING_RULES' }))
  assert.ok(html.includes('AWAITING RULES'))
  assert.ok(!html.includes('Processing has not started'))
  assert.equal((html.match(/Unavailable/g) || []).length, 4)
  console.log('Explicit answers, approval gating, version guards, scoped APIs, refresh reads, safe errors and truthful batch state passed.')
}
main().catch((error) => { console.error(error); process.exitCode = 1 })
