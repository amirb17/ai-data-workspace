// Run: node tests/backend-integration.cjs. Uses existing TypeScript, no new framework.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const ts = require('typescript')
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(
  fs.readFileSync(filename, 'utf8').replaceAll('import.meta.env', '({ VITE_API_BASE_URL: "http://localhost:8000" })'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } },
).outputText, filename)
const { request, ApiError } = require('../src/services/api/client.ts')
const { mapIdentity, getCurrentUser } = require('../src/services/api/identity.ts')
const { mapWorkspace, createWorkspace, listWorkspaces } = require('../src/services/api/workspaces.ts')
const { mapDataset, createDataset, listDatasets, getDataset } = require('../src/services/api/datasets.ts')
const { mapCompletedUpload, initiateUpload, completeUpload, putCsv } = require('../src/services/api/files.ts')
const { configureBackendScope } = require('../src/services/localScope.ts')
const { createUploadWorkflow } = require('../src/features/files/uploadWorkflow.ts')
const { reconcileCompletedUpload } = require('../src/features/ingestion/reconcileCompletedUpload.ts')
const { readDatasetFiles, fileStorageKey } = require('../src/features/files/data/storage.ts')
const { readDatasetBatches, batchStorageKey } = require('../src/features/ingestion/data/storage.ts')
const { createInitialContract } = require('../src/features/datasets/contracts/validation.ts')
const { saveDatasetContract, getDatasetContract } = require('../src/features/datasets/contracts/storage.ts')
const values = new Map()
let failingKey
global.localStorage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { if (key === failingKey) throw new Error('Quota exceeded'); values.set(key, value) } }
global.window = new EventTarget()
const context = { workspaceId: '71', datasetId: '92', userId: 8 }
const file = new File(['id,name\n1,A\n'], 'customers.csv', { type: 'application/octet-stream' })
const inspection = { fileName: file.name, fileType: 'CSV', sizeBytes: file.size, columns: ['id', 'name'], rowCount: 1, columnCount: 2, previewRows: [], contentHash: 'a'.repeat(64) }
const backend = (id = 500, duplicate = false, ctx = context) => ({
  upload_request_id: id, session_status: 'COMPLETED', is_duplicate: duplicate,
  file: { file_id: 123, file_name: 'canonical.csv', file_size: file.size, file_hash: inspection.contentHash, status: 'UPLOADED', storage_path: 'private-do-not-cache' },
  upload_request: { upload_id: id, user_id: ctx.userId, workspace_id: Number(ctx.workspaceId), dataset_id: Number(ctx.datasetId), file_id: 123, created_at: '2026-10-05T00:00:00Z' },
})
function contract(ctx = context, policy = 'ALLOW_ADDITIVE') {
  const value = createInitialContract(ctx.workspaceId, ctx.datasetId, 'Customers', inspection.columns)
  value.columns[0].required = true
  value.schemaEvolutionPolicy = policy
  saveDatasetContract(ctx.workspaceId, ctx.datasetId, value)
}
function mockWorkflow(ctx = context, options = {}) {
  const states = [], calls = { initiate: 0, put: 0, complete: 0 }
  const deps = {
    initiate: async () => { calls.initiate++; if (options.fail === 'initiate') throw new Error('initiate failed'); return { uploadRequestId: options.id ?? 500, putUrl: 'transient-only' } },
    put: async () => { calls.put++; assert.equal(readDatasetBatches(ctx.workspaceId, ctx.datasetId).length, options.previousBatches ?? 0); if (options.fail === 'put') throw new Error('put failed') },
    complete: async () => { calls.complete++; assert.equal(readDatasetBatches(ctx.workspaceId, ctx.datasetId).length, options.previousBatches ?? 0); if (options.fail === 'complete' && calls.complete === 1) throw new Error('complete failed'); return mapCompletedUpload(backend(options.id ?? 500, options.duplicate, ctx), ctx, options.id ?? 500) },
    reconcile: reconcileCompletedUpload,
  }
  return { workflow: createUploadWorkflow(ctx, (state) => states.push(state), deps), states, calls }
}
async function main() {
  assert.deepEqual(mapIdentity({ user_id: 8, display_name: 'Dev', identity_mode: 'development' }), { userId: 8, displayName: 'Dev', identityMode: 'development' })
  assert.throws(() => mapIdentity({ user_id: 0, display_name: 'Dev', identity_mode: 'development' }), /Invalid/)
  assert.equal(mapWorkspace({ workspace_id: 71, workspace_name: 'Sales', status: 'ACTIVE' }).id, 71)
  assert.equal(mapDataset({ dataset_id: 92, workspace_id: 71, dataset_name: 'Customers', status: 'ACTIVE' }, '71').id, 92)
  assert.throws(() => mapDataset({ dataset_id: 92, workspace_id: 70 }, '71'), /another workspace/)
  const requests = []
  const ws = { workspace_id: 71, workspace_name: 'Sales', status: 'ACTIVE' }
  const ds = { workspace_id: 71, dataset_id: 92, dataset_name: 'Customers', status: 'ACTIVE' }
  global.fetch = async (url, options) => {
    requests.push({ url, ...options })
    let data
    if (url.endsWith('/me')) data = { user_id: 8, display_name: 'Dev', identity_mode: 'development' }
    else if (url.endsWith('/files/initiate')) data = { upload_request_id: 500, session_status: 'INITIATED', workspace_id: 71, dataset_id: 92, user_id: 8, presigned_url: 'transient-only' }
    else if (url.endsWith('/files/complete')) data = backend()
    else if (url.endsWith('/datasets/92')) data = ds
    else if (url.endsWith('/datasets')) data = options.method === 'POST' ? ds : { workspace_id: 71, datasets: [ds] }
    else data = options.method === 'POST' ? ws : { workspaces: [ws] }
    return { ok: true, status: 200, json: async () => data }
  }
  assert.equal((await getCurrentUser()).userId, 8)
  assert.equal((await listWorkspaces())[0].id, 71)
  assert.equal((await createWorkspace({ name: 'Sales', description: '' })).id, 71)
  assert.equal((await listDatasets('71'))[0].id, 92)
  assert.equal((await createDataset('71', { name: 'Customers', description: '' })).id, 92)
  assert.equal((await getDataset('71', '92')).id, 92)
  const signal = new AbortController().signal
  await initiateUpload(context, file, signal)
  assert.equal(JSON.parse(requests.at(-1).body).content_type, 'text/csv')
  assert.equal(JSON.parse(requests.at(-1).body).user_id, 8)
  await putCsv('transient-only', file, signal)
  assert.equal(requests.at(-1).headers['Content-Type'], 'text/csv')
  assert.equal(requests.at(-1).body, file)
  const safe = await completeUpload(context, 500, signal)
  assert.deepEqual(JSON.parse(requests.at(-1).body), { upload_request_id: 500 })
  assert.equal(JSON.stringify(safe).includes('private-do-not-cache'), false)
  assert.throws(() => mapCompletedUpload(backend(), { ...context, datasetId: '93' }, 500), /does not match/)
  global.fetch = async () => ({ ok: false, status: 403, json: async () => ({ detail: 'secret' }) })
  await assert.rejects(request('/me'), (error) => error instanceof ApiError && error.status === 403 && !error.message.includes('secret'))
  global.fetch = async () => ({ ok: true, status: 200, json: async () => { throw new Error('HTML') } })
  await assert.rejects(request('/me'), /invalid response/)
  global.fetch = async () => { throw new Error('network with private URL') }
  await assert.rejects(request('/me'), /Backend unavailable/)

  values.set('datarise-workspace-71-dataset-92-files', JSON.stringify([{ id: 999 }]))
  configureBackendScope('http://localhost:8000', 8)
  assert.equal(readDatasetFiles('71', '92').length, 0, 'Old prototype numeric identities are never migrated implicitly')
  assert.equal(getDatasetContract('1', '1'), undefined, 'Backend IDs never seed mock contracts')
  let runner = mockWorkflow()
  assert.equal(await runner.workflow.accept(file, inspection, 'no-contract'), false)
  assert.equal(runner.calls.initiate, 0)
  contract()
  for (const columns of [['name'], ['unrelated']]) {
    runner = mockWorkflow()
    assert.equal(await runner.workflow.accept(file, { ...inspection, columns }, 'blocked'), false)
    assert.equal(runner.calls.initiate, 0)
  }
  contract(context, 'STRICT')
  runner = mockWorkflow()
  assert.equal(await runner.workflow.accept(file, { ...inspection, columns: ['id', 'name', 'extra'] }, 'strict'), false)
  assert.equal(runner.calls.initiate, 0)
  contract()
  for (const fail of ['initiate', 'put']) {
    runner = mockWorkflow(context, { fail })
    assert.equal(await runner.workflow.accept(file, inspection, fail), false)
    assert.equal(readDatasetFiles('71', '92').length, 0)
    assert.equal(readDatasetBatches('71', '92').length, 0)
  }
  const putScope = { ...context, datasetId: '97' }
  contract(putScope)
  const putFailure = { fail: 'put' }
  const putRetry = mockWorkflow(putScope, putFailure)
  assert.equal(await putRetry.workflow.accept(file, inspection, 'put-retry'), false)
  putFailure.fail = undefined
  assert.equal(await putRetry.workflow.accept(file, inspection, 'put-retry'), true)
  assert.deepEqual(putRetry.calls, { initiate: 1, put: 2, complete: 1 })
  const warningScope = { ...context, datasetId: '98' }
  contract(warningScope)
  const warning = mockWorkflow(warningScope)
  assert.equal(await warning.workflow.accept(file, { ...inspection, columns: ['id', 'name', 'extra'], columnCount: 3 }, 'warning'), true)
  assert.equal(readDatasetBatches('71', '98')[0].columnCount, 3)
  runner = mockWorkflow(context, { fail: 'complete' })
  assert.equal(await runner.workflow.accept(file, inspection, 'retry'), false)
  assert.equal(readDatasetFiles('71', '92').length, 0)
  assert.equal(readDatasetBatches('71', '92').length, 0)
  assert.equal(await runner.workflow.accept(file, inspection, 'retry'), true)
  assert.deepEqual(runner.calls, { initiate: 1, put: 1, complete: 2 })
  assert.deepEqual(runner.states.map((state) => state.state), ['INITIATING', 'UPLOADING', 'COMPLETING', 'FAILED', 'COMPLETING', 'SUCCESS'])
  await runner.workflow.accept(file, inspection, 'retry')
  reconcileCompletedUpload(context, safe, inspection, 'retry')
  assert.equal(readDatasetFiles('71', '92').length, 1)
  assert.equal(readDatasetBatches('71', '92').length, 1)
  assert.equal(readDatasetFiles('71', '92')[0].id, 123)
  assert.equal(readDatasetFiles('71', '92')[0].status, 'UPLOADED')
  const batch = readDatasetBatches('71', '92')[0]
  assert.equal(batch.uploadRequestId, 500)
  assert.equal(batch.sourceFileId, 123)
  assert.equal(batch.status, 'READY_TO_PROCESS')
  assert.equal(batch.validRows, null)
  reconcileCompletedUpload(context, mapCompletedUpload(backend(501, true), context, 501), inspection, 'second-logical-upload')
  assert.equal(readDatasetFiles('71', '92').length, 1, 'Canonical reuse keeps one physical file')
  assert.equal(readDatasetBatches('71', '92').length, 2, 'Separate logical uploads retain separate batches')
  assert.deepEqual(readDatasetFiles('71', '92')[0].uploadRequestIds, [500, 501])
  for (const scope of [{ ...context, datasetId: '93' }, { ...context, workspaceId: '72' }]) {
    contract(scope)
    runner = mockWorkflow(scope, { duplicate: true })
    assert.equal(await runner.workflow.accept(file, inspection, 'isolated'), true)
    assert.equal(runner.states.at(-1).isDuplicate, true)
    assert.equal(readDatasetFiles(scope.workspaceId, scope.datasetId).length, 1)
    assert.equal(readDatasetBatches(scope.workspaceId, scope.datasetId).length, 1)
  }
  assert.equal(readDatasetBatches('71', '92').length, 2)
  configureBackendScope('http://localhost:8000', 9)
  assert.equal(readDatasetFiles('71', '92').length, 0)
  configureBackendScope('http://another-backend', 8)
  assert.equal(readDatasetBatches('71', '92').length, 0)
  configureBackendScope('http://localhost:8000', 8)
  const recoveryScope = { ...context, datasetId: '94' }
  contract(recoveryScope)
  failingKey = batchStorageKey('71', '94')
  runner = mockWorkflow(recoveryScope)
  assert.equal(await runner.workflow.accept(file, inspection, 'cache-retry'), false)
  assert.equal(readDatasetFiles('71', '94').length, 1)
  assert.equal(readDatasetBatches('71', '94').length, 0)
  failingKey = undefined
  assert.equal(await runner.workflow.accept(file, inspection, 'cache-retry'), true)
  assert.deepEqual(runner.calls, { initiate: 1, put: 1, complete: 1 })
  assert.equal(readDatasetBatches('71', '94').length, 1)
  const failFileScope = { ...context, datasetId: '95' }
  contract(failFileScope)
  failingKey = fileStorageKey('71', '95')
  runner = mockWorkflow(failFileScope)
  assert.equal(await runner.workflow.accept(file, inspection, 'file-fail'), false)
  assert.equal(readDatasetBatches('71', '95').length, 0)
  failingKey = undefined
  assert.equal(await runner.workflow.accept(file, inspection, 'file-fail'), true)
  const abortScope = { ...context, datasetId: '96' }
  contract(abortScope)
  let resolvePut, abortStates = []
  const workflow = createUploadWorkflow(abortScope, (state) => abortStates.push(state), {
    initiate: async () => ({ uploadRequestId: 800, putUrl: 'transient-only' }),
    put: () => new Promise((resolve) => { resolvePut = resolve }),
    complete: async () => { throw new Error('Must not complete after disposal') },
    reconcile: reconcileCompletedUpload,
  })
  workflow.dispose(); workflow.activate() // React StrictMode setup/cleanup replay.
  const pending = workflow.accept(file, inspection, 'route-change')
  await Promise.resolve(); await Promise.resolve()
  assert.equal(await workflow.accept(file, inspection, 'route-change'), false, 'Repeated click is ignored while busy')
  workflow.dispose(); resolvePut()
  assert.equal(await pending, false)
  assert.equal(readDatasetBatches('71', '96').length, 0)
  assert.equal(abortStates.at(-1).state, 'UPLOADING')
  assert.equal([...values.values()].some((value) => value.includes('transient-only') || value.includes('private-do-not-cache')), false)
  console.log('Backend identity/API mapping, MIME, states, gated uploads, retries, canonical reuse, batch lineage, storage recovery, aborts and all scopes passed.')
}
main().catch((error) => { console.error(error); process.exitCode = 1 })
