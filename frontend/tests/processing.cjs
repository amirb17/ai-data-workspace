const assert = require('node:assert/strict')
const fs = require('node:fs')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { MemoryRouter, Routes, Route } = require('react-router-dom')
for (const extension of ['.ts', '.tsx']) require.extensions[extension] = (module, filename) => module._compile(ts.transpileModule(
  fs.readFileSync(filename, 'utf8').replaceAll('import.meta.env', '({ VITE_API_BASE_URL: "http://localhost:8000" })'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } },
).outputText, filename)
const api = require('../src/services/api/rules.ts')
const state = require('../src/features/ingestion/processingState.ts')
const presentation = require('../src/features/ingestion/processingPresentation.ts')
const { ProcessingStages } = require('../src/features/ingestion/components/ProcessingStages.tsx')
const { ProcessingDetails } = require('../src/features/ingestion/components/ProcessingDetails.tsx')
const { ProcessingResult } = require('../src/features/ingestion/components/ProcessingResult.tsx')
const { IngestionBatchCard } = require('../src/features/ingestion/components/IngestionBatchCard.tsx')
const { DeliveryRuleBridge } = require('../src/features/ingestion/components/DeliveryRuleBridge.tsx')
const { DatasetProcessingSummary } = require('../src/features/ingestion/components/DatasetProcessingSummary.tsx')
const { ProcessingActivity } = require('../src/features/ingestion/components/ProcessingActivity.tsx')
const { DatasetBreadcrumbs } = require('../src/features/datasets/components/DatasetBreadcrumbs.tsx')
const context = { upload_request_id:40,workspace_id:10,dataset_id:20,file_id:30,status:'READY_FOR_SILVER',can_continue:true,
  dataset_version_file_id:60,dataset_version_id:50,dataset_version_number:1,rule_version:1,rule_state:'FINALIZED',rules_reused:true,
  stages:{bronze:'SUCCESS',rules:'FINALIZED',silver:'PENDING',gold:'PENDING'},input_rows:3,column_count:2,
  valid_rows:null,rejected_rows:null,output_rows:null,duplicate_rows:null,updated_rows:null,latest_attempt:null,
  quarantine_available:false,issue_summary:[],started_at:null,completed_at:null,error_summary:null,gold_skip_reason:null }
const batch = {id:'upload-40',sourceFileId:30,uploadRequestId:40,sourceFileName:'orders.csv',rowCount:3,columnCount:2,createdAt:'2026-10-06T00:00:00Z',validRows:999,rejectedRows:999,duplicateRows:999,updatedRows:999}
const render = (component, props) => renderToStaticMarkup(React.createElement(MemoryRouter,null,React.createElement(component, props)))
const delivery = c => ({source_file_name:'orders.csv',created_at:batch.createdAt,context:c})
const dataset = {workspace_id:10,dataset_id:20,deliveries:[delivery(context),delivery({...context,upload_request_id:41})],
  summary:{total:2,pending:2,processing:0,awaiting_rules:0,successful:0,failed:0,needs_attention:0}}
const success = {...context,status:'SUCCESS',can_continue:false,valid_rows:3,rejected_rows:0,output_rows:3,
  stages:{bronze:'SUCCESS',rules:'FINALIZED',silver:'SUCCESS',gold:'SUCCESS'}}
const mixed = {...success,valid_rows:2,rejected_rows:1,output_rows:2,quarantine_available:true,issue_summary:[{rule_type:'NOT_NULL',violation_count:1}]}
const rejected = {...mixed,status:'SUCCESS_WITH_WARNINGS',valid_rows:0,rejected_rows:3,output_rows:0,
  gold_skip_reason:'No valid rows were available for Gold publication.',stages:{...mixed.stages,gold:'SKIPPED'}}
const bridge = c => render(DeliveryRuleBridge,{batch,context:c,workspaceId:'10',datasetId:'20',reload:()=>{},datasetBusy:false})
async function main() {
  assert.equal(state.executionAction(context),null)
  assert.equal(state.executionAction(success),null)
  assert.equal(state.executionAction(rejected),null)
  assert.equal(state.executionAction({...context,status:'SILVER_FAILED'}),'Retry Processing')
  assert.equal(state.executionAction({...context,status:'GOLD_FAILED'}),'Retry Gold')
  assert.equal(state.executionAction({...context,status:'AWAITING_RULES',can_continue:false}),null)
  for (const status of ['BRONZE_PROCESSING','SILVER_PROCESSING','GOLD_PROCESSING']) assert.ok(state.isRunning(status))
  assert.equal(state.isRunning('SUCCESS'),false)
  const breadcrumb = render(DatasetBreadcrumbs,{workspaceId:'10',datasetId:'20',workspaceName:'Sales Analytics',datasetName:'Orders',section:'Processing'})
  for (const text of ['aria-label="Breadcrumb"','Sales Analytics','Orders','aria-current="page"','flex-wrap']) assert.ok(breadcrumb.includes(text))
  assert.ok(breadcrumb.includes('href="/app/workspaces/10/datasets/20"'))
  assert.ok(!breadcrumb.includes('>10<') && !breadcrumb.includes('>20<'))
  const cards = [bridge(context),bridge({...context,status:'READY_TO_PROCESS',dataset_version_file_id:null,can_continue:false})].join('')
  assert.ok(!cards.includes('Batch ') && !cards.includes('Continue Processing') && !cards.includes('Prepare Rules'))
  assert.ok(cards.includes('<h3') && cards.includes('orders.csv</h3>') && cards.includes('<details') && cards.includes('<summary'))
  assert.ok(cards.includes('Delivery ID / upload request') && !cards.includes('999'))
  const clean = bridge(success)
  assert.ok(clean.includes('Completed successfully') && !clean.includes('Completed with warnings') && !clean.includes('View Data Quality'))
  assert.ok(!clean.includes('Retry Processing') && clean.includes('Approved Rule Version 1 reused'))
  const mixedHtml = bridge(mixed)
  assert.ok(mixedHtml.includes('Completed with warnings') && mixedHtml.includes('1 row needs attention') && mixedHtml.includes('View Data Quality'))
  assert.ok(render(ProcessingStages,{context:mixed}).includes('Complete') && !render(ProcessingStages,{context:mixed}).includes('Skipped'))
  const rejectedHtml = bridge(rejected)
  for (const text of ['Completed with warnings','Skipped','No valid rows were available for analytics output.','View Data Quality','0.0%','100.0%']) assert.ok(rejectedHtml.includes(text))
  assert.ok(!rejectedHtml.includes('role="alert"') && !rejectedHtml.includes('Retry Gold'))
  const result = render(ProcessingResult,{context:mixed})
  for (const text of ['Processing Result','Input rows','Quarantined rows','Gold output rows','66.7%','33.3%','Duplicates','Updated rows','—']) assert.ok(result.includes(text))
  for (const [numerator,total] of [[null,3],[1,null],[0,0],[-1,3],[4,3],[NaN,3]]) assert.equal(presentation.rateLabel(numerator,total),'—')
  assert.equal(presentation.rateLabel(0,3),'0.0%')
  const awaiting = bridge({...context,status:'AWAITING_RULES',can_continue:false,rule_state:'DRAFT',rule_version:0,stages:{...context.stages,rules:'DRAFT'}})
  for (const text of ['Rules need your review','Bronze profiling is complete','waiting for your decisions','Review Rules','Needs attention']) assert.ok(awaiting.includes(text))
  assert.ok(!clean.includes('Review Rules') && !clean.includes('Finalize / Approve'))
  const silverFailed = bridge({...context,status:'SILVER_FAILED',error_summary:'Silver validation failed.',stages:{...context.stages,silver:'FAILED'}})
  for (const text of ['Processing needs attention','Failed','source file is safe','do not need to upload it again','Retry Processing']) assert.ok(silverFailed.includes(text))
  const goldFailed = bridge({...mixed,status:'GOLD_FAILED',can_continue:true,error_summary:'Gold publication failed.',stages:{...mixed.stages,gold:'FAILED'}})
  for (const text of ['Gold publication failed','Retry Gold','will reuse successful Silver','Failed']) assert.ok(goldFailed.includes(text))
  const summary = render(DatasetProcessingSummary,{data:dataset,busy:false,process:()=>{}})
  assert.equal((summary.match(/Process Pending Deliveries \(2\)/g)||[]).length,1)
  assert.ok(!summary.includes('disabled=""') && !summary.includes('Batches'))
  const empty = {...dataset,deliveries:[],summary:{...dataset.summary,total:0,pending:0}}
  const done = {...dataset,deliveries:[delivery(success)],summary:{...dataset.summary,total:1,pending:0,successful:1}}
  assert.ok(!render(DatasetProcessingSummary,{data:empty,busy:false,process:()=>{}}).includes('Process Pending Deliveries'))
  assert.ok(render(DatasetProcessingSummary,{data:done,busy:false,process:()=>{}}).includes('Everything is up to date'))
  const needsAttention = {...done,deliveries:[delivery(mixed)]}
  assert.equal(presentation.overviewWarnings(needsAttention),1)
  assert.equal(presentation.overviewAttention(needsAttention),1)
  assert.ok(render(DatasetProcessingSummary,{data:needsAttention,busy:false,process:()=>{}}).includes('Review deliveries that need attention'))
  const running = {...context,status:'SILVER_PROCESSING',stages:{...context.stages,silver:'PROCESSING'}}
  const active = render(ProcessingActivity,{data:{...dataset,deliveries:[delivery(running)]},busy:true})
  for (const text of ['Processing orders.csv','Validating and cleaning rows','Processing','aria-live="polite"','grid-cols-1','sm:grid-cols-4']) assert.ok(active.includes(text))
  assert.ok(!active.includes('%') && !active.includes('Delivery 1 of'))
  assert.equal(render(ProcessingActivity,{data:done,busy:false}),'')
  assert.ok(render(ProcessingActivity,{data:dataset,busy:true}).includes('Waiting for the next stage update'))
  const privateFields = {...mixed,storage_path:'SECRET_STORAGE',presigned_url:'SECRET_SIGNED',traceback:'SECRET_TRACE'}
  assert.ok(!render(ProcessingDetails,{context:privateFields}).includes('SECRET_'))
  // Render real route/page components with resource boundaries supplied by fixtures.
  const hook = require('../src/features/ingestion/useDatasetProcessing.ts')
  const resource = require('../src/services/api/useApiResource.ts')
  const oldHook = hook.useDatasetProcessing, oldResource = resource.useApiResource
  const { DatasetProcessingPage } = require('../src/pages/Dataset/Processing/DatasetProcessingPage.tsx')
  const { DatasetRulesPage } = require('../src/pages/Dataset/Rules/DatasetRulesPage.tsx')
  const { DatasetDetailPage } = require('../src/pages/Dataset/DatasetDetailPage.tsx')
  const { WorkspaceDetailPage } = require('../src/pages/Workspace/WorkspaceDetailPage.tsx')
  let currentData = empty
  hook.useDatasetProcessing = (w,d) => ({data:presentation.ownsProcessing(currentData,w,d)?currentData:undefined,error:'',busy:false,process:()=>{},reload:()=>{},onRequestActivity:()=>{}})
  resource.useApiResource = key => ({data:key.startsWith('rule-deliveries:')?dataset:key.startsWith('workspace:')?{workspace:{id:10,name:'Sales Analytics',status:'ACTIVE'},datasets:[]}:{id:20,name:'Orders',description:'Order transactions',status:'ACTIVE'},retry:()=>{}})
  const page = (url='/app/workspaces/10/datasets/20/processing') => renderToStaticMarkup(React.createElement(MemoryRouter,{initialEntries:[url]},React.createElement(Routes,null,
    React.createElement(Route,{path:'/app/workspaces/:workspaceId',element:React.createElement(WorkspaceDetailPage)},
      React.createElement(Route,{path:'datasets/:datasetId',element:React.createElement(DatasetDetailPage)},
        React.createElement(Route,{path:'processing',element:React.createElement(DatasetProcessingPage)}),
        React.createElement(Route,{path:'rules',element:React.createElement(DatasetRulesPage)}))))))
  try {
    const emptyPage = page()
    assert.ok(emptyPage.includes('No deliveries yet') && emptyPage.includes('Go to Files'))
    assert.ok(!emptyPage.includes('Process Pending Deliveries') && !emptyPage.includes('<h1 class="text-3xl'))
    assert.ok(emptyPage.includes('Sales Analytics') && emptyPage.includes('Orders') && emptyPage.includes('aria-label="Dataset sections"'))
    currentData = done
    assert.ok(page().includes('Everything is up to date'))
    currentData = dataset
    assert.equal((page().match(/Process Pending Deliveries/g)||[]).length,1)
    const otherScope = page('/app/workspaces/11/datasets/21/processing')
    assert.ok(!otherScope.includes('orders.csv') && otherScope.includes('Loading delivery status'))
    assert.equal(presentation.ownsProcessing(dataset,'11','20'),false)
    assert.equal(presentation.ownsProcessing(dataset,'10','21'),false)
    const rules = page('/app/workspaces/10/datasets/20/rules?upload=40')
    assert.ok(rules.includes('Source delivery') && rules.includes('orders.csv') && rules.includes('Loading delivery rules'))
    assert.ok(!rules.includes('Upload #40') && !rules.includes('not in this dataset'))
    assert.ok(page('/app/workspaces/10/datasets/20/rules?upload=999').includes('The selected delivery is not in this dataset'))
  } finally { hook.useDatasetProcessing=oldHook; resource.useApiResource=oldResource }
  let responseState = context
  const calls=[]
  global.fetch = async (url,options) => { calls.push({url,...options}); return new Response(JSON.stringify(responseState)) }
  assert.equal((await api.getProcessingContext('10','20',40)).status,'READY_FOR_SILVER')
  responseState = success
  assert.equal((await api.continueProcessing('10','20',40)).status,'SUCCESS')
  assert.equal((await api.getProcessingContext('10','20',40)).output_rows,3)
  assert.equal(calls[1].method,'POST'); assert.equal(calls[1].body,undefined)
  for (const mismatch of [{workspace_id:11},{dataset_id:21},{upload_request_id:41}]) {
    responseState = {...success,...mismatch}; await assert.rejects(api.continueProcessing('10','20',40),/another|mismatch/)
  }
  responseState = dataset
  assert.equal((await api.datasetProcessing('10','20',true)).summary.pending,2)
  assert.ok(calls.at(-1).url.endsWith('/workspaces/10/datasets/20/processing/pending')); assert.equal(calls.at(-1).body,undefined)
  responseState = {...dataset,deliveries:[delivery({...context,dataset_id:21})]}
  await assert.rejects(api.datasetProcessing('10','20'),/another/)
  console.log('Named breadcrumbs, real headers, delivery terminology, primary actions, stages, result variants, truthful rates, rules/retries, empty/ready views, responsive structure, private metadata filtering, scope isolation and refresh checks passed.')
}
main().catch(error => { console.error(error);process.exitCode=1 })
