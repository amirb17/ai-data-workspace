// Run with node tests/file-content-duplicates.cjs.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const ts = require('typescript')
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, filename)
const { hashFileContent } = require('../src/features/files/utils/hashFileContent.ts')
const { inspectCsv } = require('../src/features/files/utils/inspectCsv.ts')
const { acceptInspectedCsv } = require('../src/features/ingestion/acceptInspectedCsv.ts')
const { readDatasetFiles, findFileByContentHash, acceptDatasetFile, DuplicateFileContentError } = require('../src/features/files/data/storage.ts')
const { readDatasetBatches, batchStorageKey } = require('../src/features/ingestion/data/storage.ts')
const { createInitialContract } = require('../src/features/datasets/contracts/validation.ts')
const { saveDatasetContract, contractStorageKey } = require('../src/features/datasets/contracts/storage.ts')
const values = new Map()
let failureKey
localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => {
  if (key === failureKey) throw new Error('quota')
  values.set(key, value)
} }
global.window = new EventTarget()
async function inspection(name, text) {
  const file = new File([text], name)
  return { ...await inspectCsv(file), contentHash: await hashFileContent(file) }
}
async function run() {
  assert.equal(await hashFileContent(new File(['abc'], 'anything.csv')), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  const original = await inspection('orders.csv', 'id,amount\n1,10')
  const renamed = await inspection('orders_copy.csv', 'id,amount\n1,10')
  const corrected = await inspection('orders.csv', 'id,amount\n1,20')
  assert.equal(original.contentHash, renamed.contentHash)
  assert.notEqual(original.contentHash, corrected.contentHash)
  assert.notEqual(original.contentHash, await hashFileContent(new File(['id,amount\r\n1,10'], 'orders.csv')), 'Hash exact bytes, including line endings')
  for (const scope of [['1','99'], ['1','100'], ['2','99']]) saveDatasetContract(...scope, createInitialContract(...scope, 'Orders', original.columns))
  const contractBefore = values.get(contractStorageKey('1','99'))
  const first = acceptInspectedCsv('1','99',original,'event-1')
  assert.equal(readDatasetFiles('1','99')[0].contentHash, original.contentHash)
  assert.equal(acceptInspectedCsv('1','99',original,'event-1').id, first.id)
  assert.throws(() => acceptInspectedCsv('1','99',original,'event-2'), DuplicateFileContentError)
  assert.throws(() => acceptInspectedCsv('1','99',renamed,'event-3'), DuplicateFileContentError)
  assert.throws(() => acceptDatasetFile('1','99',renamed,'bypass-ui'), DuplicateFileContentError)
  assert.equal(readDatasetFiles('1','99').length,1)
  assert.equal(readDatasetBatches('1','99').length,1)
  assert.equal(findFileByContentHash('1','99',original.contentHash).id, first.sourceFileId)
  acceptInspectedCsv('1','99',corrected,'correction')
  assert.equal(readDatasetFiles('1','99').length,2)
  assert.equal(readDatasetBatches('1','99').length,2)
  for (const scope of [['1','100'], ['2','99']]) {
    acceptInspectedCsv(...scope, original,'event-1')
    assert.equal(readDatasetFiles(...scope).length,1)
    assert.equal(readDatasetBatches(...scope).length,1)
  }
  assert.equal(values.get(contractStorageKey('1','99')), contractBefore)
  assert.equal(readDatasetFiles('1','1').length,2, 'Legacy mock entries remain readable')
  assert.equal(findFileByContentHash('1','1',original.contentHash),undefined)
  saveDatasetContract('1','1',createInitialContract('1','1','Orders',original.columns))
  acceptInspectedCsv('1','1',original,'legacy-new')
  assert.equal(readDatasetFiles('1','1').length,3, 'No filename/size guesses for legacy entries')
  assert.equal(readDatasetBatches('1','1').length,1)
  const retry = await inspection('retry.csv','id,amount\n2,30')
  failureKey = batchStorageKey('1','99')
  assert.throws(() => acceptInspectedCsv('1','99',retry,'retry-event'), /Retry Accept File/)
  assert.equal(readDatasetFiles('1','99').length,3)
  assert.equal(readDatasetBatches('1','99').length,2)
  failureKey = undefined
  acceptInspectedCsv('1','99',retry,'retry-event')
  assert.equal(readDatasetFiles('1','99').length,3)
  assert.equal(readDatasetBatches('1','99').length,3)
  assert.throws(() => acceptInspectedCsv('1','99',retry,'another-upload'),DuplicateFileContentError)
  assert.throws(() => acceptInspectedCsv('1','99',{...original,contentHash:undefined},'unhashed'),/verification/)
  console.log('SHA-256, filename independence, content changes, scoped dedupe, legacy reads, repeated clicks, storage guard and batch-retry checks passed.')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
