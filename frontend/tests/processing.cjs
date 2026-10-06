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
const { executionAction, isRunning } = require('../src/features/ingestion/processingState.ts')
const { ProcessingDetails } = require('../src/features/ingestion/components/ProcessingDetails.tsx')
const { IngestionBatchCard } = require('../src/features/ingestion/components/IngestionBatchCard.tsx')
const context = { upload_request_id:40,workspace_id:10,dataset_id:20,file_id:30,status:'READY_FOR_SILVER',can_continue:true,
  rule_version:1,rule_state:'FINALIZED',rules_reused:true,stages:{bronze:'SUCCESS',rules:'FINALIZED',silver:'PENDING',gold:'PENDING'},
  input_rows:3,valid_rows:null,rejected_rows:null,output_rows:null,duplicate_rows:null,updated_rows:null,
  latest_attempt:null,quarantine_available:false,issue_summary:[],started_at:null,completed_at:null,error_summary:null }
const batch = {id:'upload-40',sourceFileId:30,uploadRequestId:40,sourceFileName:'a.csv',rowCount:3,columnCount:2,createdAt:'2026-10-05T00:00:00Z',validRows:999,rejectedRows:999,duplicateRows:999,updatedRows:999}
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props))
async function main() {
  assert.equal(executionAction(context), null)
  assert.equal(executionAction({...context,status:'SUCCESS'}), null)
  assert.equal(executionAction({...context,status:'SUCCESS_WITH_WARNINGS'}), null)
  assert.equal(executionAction({...context,status:'SILVER_FAILED'}), 'Retry Processing')
  assert.equal(executionAction({...context,status:'GOLD_FAILED'}), 'Retry Processing')
  assert.equal(executionAction({...context,status:'AWAITING_RULES',can_continue:false}), null)
  for (const status of ['BRONZE_PROCESSING','SILVER_PROCESSING','GOLD_PROCESSING']) assert.ok(isRunning(status))
  assert.equal(isRunning('SUCCESS'),false)
  const waiting = render(ProcessingDetails,{context})
  assert.ok(waiting.includes('Bronze') === false && waiting.includes('bronze'))
  assert.ok(waiting.includes('silver') && waiting.includes('gold') && waiting.includes('PENDING'))
  assert.ok(!waiting.includes('Processing complete'))
  const running = render(ProcessingDetails,{context:{...context,status:'SILVER_PROCESSING',stages:{...context.stages,silver:'PROCESSING'}}})
  assert.ok(running.includes('Running') && !running.includes('Processing complete'))
  const success = {...context,status:'SUCCESS',can_continue:false,valid_rows:2,rejected_rows:1,output_rows:2,quarantine_available:true,issue_summary:[{rule_type:'NOT_NULL',violation_count:1}],stages:{...context.stages,silver:'SUCCESS',gold:'SUCCESS'}}
  const ready = render(ProcessingDetails,{context:success})
  assert.ok(ready.includes('Processing complete') && ready.includes('Quarantine available') && ready.includes('1 rejected rows'))
  const card = render(IngestionBatchCard,{batch,processingStatus:success.status,context:success})
  assert.ok(card.includes('Output rows') && !card.includes('999'))
  assert.equal((card.match(/—/g)||[]).length,2)
  const failed = render(ProcessingDetails,{context:{...context,status:'GOLD_FAILED',error_summary:'Gold publication failed.'}})
  const skipped = {...success,status:'SUCCESS_WITH_WARNINGS',valid_rows:0,rejected_rows:3,output_rows:0,gold_skip_reason:'No valid rows were available for Gold publication.',stages:{...success.stages,gold:'SKIPPED'}}
  const warning = render(ProcessingDetails,{context:skipped})
  assert.ok(warning.includes('Skipped') && warning.includes('Completed with warnings') && warning.includes('3 rejected rows'))
  assert.ok(!warning.includes('role="alert"') && !warning.includes('Gold publication failed'))
  const { DatasetProcessingSummary } = require('../src/features/ingestion/components/DatasetProcessingSummary.tsx')
  const dataset = {workspace_id:10,dataset_id:20,deliveries:[{context},{context:{...context,upload_request_id:41}}],summary:{total:2,pending:2,processing:0,awaiting_rules:0,successful:0,failed:0,needs_attention:0}}
  const summary = render(DatasetProcessingSummary,{data:dataset,busy:false,process:()=>{}})
  assert.equal((summary.match(/Process Pending Batches/g)||[]).length,1)
  assert.ok(!summary.includes('disabled=""'))
  assert.ok(render(DatasetProcessingSummary,{data:{...dataset,summary:{...dataset.summary,pending:0}},busy:false,process:()=>{}}).includes('disabled=""'))
  const { MemoryRouter } = require('react-router-dom')
  const { DeliveryRuleBridge } = require('../src/features/ingestion/components/DeliveryRuleBridge.tsx')
  const bridge = state => React.createElement(DeliveryRuleBridge,{batch,workspaceId:'10',datasetId:'20',context:state,reload:()=>{},datasetBusy:false})
  const pendingPage = renderToStaticMarkup(React.createElement(MemoryRouter,null,
    React.createElement(DatasetProcessingSummary,{data:dataset,busy:false,process:()=>{}}),
    bridge({...context,status:'READY_TO_PROCESS',dataset_version_file_id:null,can_continue:false}),bridge(context)))
  assert.equal((pendingPage.match(/Process Pending Batches/g)||[]).length,1)
  assert.ok(!pendingPage.includes('Continue Processing') && !pendingPage.includes('Prepare Rules (Bronze)'))
  const warningDelivery = renderToStaticMarkup(React.createElement(MemoryRouter,null,bridge(skipped)))
  assert.ok(warningDelivery.includes('View Data Quality') && !warningDelivery.includes('Retry Processing'))
  assert.ok(renderToStaticMarkup(React.createElement(MemoryRouter,null,bridge({...context,status:'GOLD_FAILED'}))).includes('Retry Processing'))
  assert.ok(failed.includes('role="alert"') && failed.includes('Gold publication failed.'))
  let state = context
  const calls=[]
  global.fetch = async (url,options) => { calls.push({url,...options}); return new Response(JSON.stringify(state)) }
  assert.equal((await api.getProcessingContext('10','20',40)).status,'READY_FOR_SILVER')
  state = success
  assert.equal((await api.continueProcessing('10','20',40)).status,'SUCCESS')
  assert.equal((await api.getProcessingContext('10','20',40)).output_rows,2)
  assert.equal(calls[1].method,'POST')
  assert.ok(calls[1].url.endsWith('/uploads/40/continue?workspace_id=10&dataset_id=20'))
  assert.equal(calls[1].body,undefined)
  for (const mismatch of [{workspace_id:11},{dataset_id:21},{upload_request_id:41}]) {
    state = {...success,...mismatch}
    await assert.rejects(api.continueProcessing('10','20',40),/another|mismatch/)
  }
  state = dataset
  assert.equal((await api.datasetProcessing('10','20',true)).summary.pending,2)
  assert.ok(calls.at(-1).url.endsWith('/workspaces/10/datasets/20/processing/pending'))
  assert.equal(calls.at(-1).body,undefined)
  state = {...dataset,deliveries:[{context:{...context,dataset_id:21}}]}
  await assert.rejects(api.datasetProcessing('10','20'),/another/)
  console.log('Processing actions, stages, truthful metrics, safe failure, scoped continuation and refresh recovery passed.')
}
main().catch(error => { console.error(error);process.exitCode=1 })
