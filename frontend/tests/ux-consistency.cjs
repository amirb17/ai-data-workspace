const assert = require('node:assert/strict')
const fs = require('node:fs')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { MemoryRouter, Routes, Route, Outlet } = require('react-router-dom')
for (const extension of ['.ts','.tsx']) require.extensions[extension] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8').replaceAll('import.meta.env','({ VITE_API_BASE_URL:"http://localhost:8000" })'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename)
const resource = require('../src/services/api/useApiResource.ts')
let result = {}
resource.useApiResource = (key) => key.startsWith('incremental:') ? {data:{policies:[],applications:[],schema_versions:[],current_state:null},retry:()=>{},loading:false} : ({ ...result,retry:()=>{},loading:!result.data })
const storage = new Map()
global.localStorage = {getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}
global.window = {dispatchEvent:()=>{}}
const { IdentityContext } = require('../src/services/identityContext.ts')
const { configureBackendScope } = require('../src/services/localScope.ts')
configureBackendScope('http://localhost:8000',1)
const { StatusBadge } = require('../src/components/ui/StatusBadge.tsx')
const { datasetGuidance } = require('../src/features/datasets/datasetGuidance.ts')
const { DatasetList } = require('../src/features/datasets/components/DatasetList.tsx')
const { HomePage } = require('../src/pages/Home/HomePage.tsx')
const { WorkspacesPage } = require('../src/pages/Workspaces/WorkspacesPage.tsx')
const { WorkspaceOverviewPage } = require('../src/pages/Workspace/Overview/WorkspaceOverviewPage.tsx')
const { DatasetOverviewPage } = require('../src/pages/Dataset/Overview/DatasetOverviewPage.tsx')
const { DatasetFilesPage } = require('../src/pages/Dataset/Files/DatasetFilesPage.tsx')
const { DatasetContractPage } = require('../src/pages/Dataset/Contract/DatasetContractPage.tsx')
const { DatasetContractSummary } = require('../src/features/datasets/contracts/components/DatasetContractSummary.tsx')
const { DatasetContractForm } = require('../src/features/datasets/contracts/components/DatasetContractForm.tsx')
const { createInitialContract } = require('../src/features/datasets/contracts/validation.ts')
const { saveDatasetContract } = require('../src/features/datasets/contracts/storage.ts')
const { RuleSituation } = require('../src/pages/Dataset/Rules/DatasetRulesPage.tsx')
const { DatasetDataQualityPage } = require('../src/pages/Dataset/DataQuality/DatasetDataQualityPage.tsx')
const { DatasetAnalyticsPage } = require('../src/pages/Dataset/Analytics/DatasetAnalyticsPage.tsx')
const { DuplicateFileNotice } = require('../src/features/files/components/DuplicateFileNotice.tsx')
const { UploadFileDialog } = require('../src/features/files/components/UploadFileDialog.tsx')
const { uploadStep } = require('../src/features/files/uploadStep.ts')
const { CreateDatasetDialog } = require('../src/features/datasets/components/CreateDatasetDialog.tsx')
const { CreateWorkspaceDialog } = require('../src/features/workspaces/components/CreateWorkspaceDialog.tsx')
const { NotFoundPage } = require('../src/components/ui/NotFoundPage.tsx')
const { DatasetDetailPage } = require('../src/pages/Dataset/DatasetDetailPage.tsx')
const { DatasetReadiness } = require('../src/features/datasets/components/DatasetReadiness.tsx')
const context = {workspace_id:10,dataset_id:20,upload_request_id:77,file_id:31,status:'READY_TO_PROCESS',input_rows:null,column_count:null,valid_rows:null,rejected_rows:null,output_rows:null,stages:{bronze:'PENDING',rules:'PENDING',silver:'PENDING',gold:'PENDING'},rule_state:null,rule_version:null,rules_reused:false}
const delivery = {source_file_name:'customers.csv',created_at:'2026-10-06T00:00:00Z',size_bytes:24,context}
const empty = {workspace_id:10,dataset_id:20,deliveries:[],summary:{total:0,pending:0,processing:0,awaiting_rules:0,successful:0,failed:0,needs_attention:0}}
const pending = {...empty,deliveries:[delivery],summary:{...empty.summary,total:1,pending:1}}
const dataset = {id:20,name:'Customers',description:'Customer records',status:'ACTIVE',updatedAt:'Unavailable'}
const render = (Component,props={},url='/app/workspaces/10/datasets/20/files',outlet=dataset) => renderToStaticMarkup(React.createElement(IdentityContext.Provider,{value:{userId:1,displayName:'Test'}},React.createElement(MemoryRouter,{initialEntries:[url]},React.createElement(Routes,null,React.createElement(Route,{path:'/app/workspaces/:workspaceId',element:React.createElement(Outlet,{context:outlet})},React.createElement(Route,{path:'datasets/:datasetId/*',element:React.createElement(Component,props)}))))))
// Use the same renderer for standalone pages while supplying real router identities.
for (const [status,label] of [['ACTIVE','Active'],['AWAITING_RULES','Rules need review'],['SUCCESS','Completed'],['FAILED','Needs attention'],['READY_FOR_SILVER','Ready to continue']]) {
  const html=render(StatusBadge,{status}); assert.ok(html.includes(label)); assert.ok(html.includes('aria-hidden="true"')); assert.ok(!html.includes('>'+status+'<'))
}
result={data:[]}
assert.ok(render(WorkspacesPage).includes('No workspaces yet'))
assert.equal((render(WorkspacesPage).match(/>Create Workspace</g)||[]).length,1)
assert.ok(render(HomePage).includes('Your first workspace starts here'))
result={}
assert.ok(render(WorkspacesPage).includes('Loading workspaces'))
result={error:'Could not load workspaces.'}
assert.ok(render(WorkspacesPage).includes('role="alert"') && render(WorkspacesPage).includes('Retry'))
result={data:[{id:10,name:'Sales',description:'Sales records',status:'ACTIVE',updatedAt:'Unavailable'}]}
assert.ok(render(HomePage).includes('Sales') && !render(HomePage).includes('Processing Pipeline'))
const workspace=render(WorkspaceOverviewPage,{},undefined,{datasets:[dataset]})
assert.ok(workspace.includes('Add Dataset') && workspace.includes('Customers') && !workspace.includes('dataset-version'))
assert.ok(render(DatasetList,{datasets:[],workspaceId:'10'}).includes('No datasets yet'))
assert.ok(render(DatasetList,{datasets:[],workspaceId:'10',filtered:true}).includes('Change the search'))
assert.ok(render(CreateDatasetDialog,{open:true,onClose:()=>{},onCreate:async()=>{}}).includes('Monthly files for the same entity belong in the same dataset'))
assert.ok(render(CreateWorkspaceDialog,{open:true,onClose:()=>{},onCreate:async()=>{}}).includes('Description (optional)'))
assert.equal(datasetGuidance(empty,false).label,'Add Delivery')
assert.equal(datasetGuidance(pending,false).label,'Configure Contract')
assert.equal(datasetGuidance(pending,true).label,'Go to Processing')
const awaiting={...context,status:'AWAITING_RULES',rule_state:'DRAFT'}
assert.equal(datasetGuidance({...pending,deliveries:[{...delivery,context:awaiting}]},true).label,'Review Rules')
const success={...context,status:'SUCCESS',rule_state:'FINALIZED',rule_version:1,rules_reused:true,input_rows:2,valid_rows:2,rejected_rows:0,output_rows:2,stages:{bronze:'SUCCESS',rules:'FINALIZED',silver:'SUCCESS',gold:'SUCCESS'}}
const done={...empty,deliveries:[{...delivery,context:success}],summary:{...empty.summary,total:1,successful:1}}
assert.equal(datasetGuidance(done,true).label,'View Analytics')
result={data:empty}
assert.ok(render(DatasetOverviewPage).includes('Add Delivery'))
assert.ok(render(DatasetFilesPage).includes('No deliveries yet'))
assert.equal((render(DatasetFilesPage).match(/>Add Delivery</g)||[]).length,1)
const initial=createInitialContract('10','20','Customers',['id','name'])
const setup=render(DatasetContractPage)
assert.ok(setup.includes('No contract configured') && setup.includes('Configure Contract') && setup.includes('files?setup=1'))
saveDatasetContract('10','20',initial)
assert.ok(render(DatasetContractPage).includes('Edit Contract'))
assert.ok(render(DatasetContractSummary,{contract:initial}).includes('2 columns · 0 required'))
assert.ok(render(DatasetContractForm,{initialContract:initial,onSave:()=>{},onCancel:()=>{}}).includes('Save Contract'))
result={data:pending}
assert.ok(render(DatasetReadiness,{workspaceId:'10',datasetId:20}).includes('1 pending'))
assert.ok(render(DatasetReadiness,{workspaceId:'99',datasetId:20}).includes('Loading delivery summary'))
const files=render(DatasetFilesPage)
assert.ok(files.includes('customers.csv') && files.includes('24 bytes') && files.includes('Not started'))
assert.ok(files.includes('<details') && files.includes('>Remove Delivery<') && files.includes('historical lineage'))
assert.ok(!files.includes('File #31') && !files.includes('s3://'))
result={data:{...pending,deliveries:[delivery,{...delivery,source_file_name:'second-name.csv',context:{...context,upload_request_id:78}}]}}
const shared=render(DatasetFilesPage)
assert.equal((shared.match(/<article/g)||[]).length,2)
assert.ok(shared.includes('customers.csv') && shared.includes('second-name.csv'))
assert.equal((shared.match(/>Remove Delivery</g)||[]).length,2)
result={data:done}
const completedFiles=render(DatasetFilesPage)
assert.ok(completedFiles.includes('Completed') && completedFiles.includes('>Archive Delivery<') && completedFiles.includes('historical lineage'))
assert.ok(render(DatasetDataQualityPage).includes('No data-quality issues detected'))
assert.ok(render(DatasetAnalyticsPage).includes('Dataset Analytics'))
assert.ok(!render(DatasetAnalyticsPage).includes('Delivery processing output is available'))
const rejected={...success,status:'SUCCESS_WITH_WARNINGS',valid_rows:0,rejected_rows:2,output_rows:0,stages:{...success.stages,gold:'SKIPPED'}}
result={data:{...done,deliveries:[{...delivery,context:rejected}]}}
assert.ok(render(DatasetDataQualityPage).includes('2 quarantined rows') && render(DatasetDataQualityPage).includes('coming in the next stage'))
assert.ok(!render(DatasetAnalyticsPage).includes('after successful processing produces valid output'))
result={data:pending}
assert.ok(!render(DatasetDataQualityPage).includes('No data-quality issues detected'))
assert.ok(render(RuleSituation,{context:success,reviewing:false,reviewHref:'rules'}).includes('Approved Rule Version 1 reused'))
assert.ok(!render(RuleSituation,{context:success,reviewing:false,reviewHref:'rules'}).includes('Review Rules'))
assert.ok(render(RuleSituation,{context:awaiting,reviewing:false,reviewHref:'rules'}).includes('Review Rules'))
assert.ok(!render(RuleSituation,{context:awaiting,reviewing:true,reviewHref:'rules'}).includes('>Review Rules<'))
const duplicate=render(DuplicateFileNotice,{file:{id:31,fileName:'customers.csv'},workspaceId:'10',datasetId:'20'})
assert.ok(duplicate.includes('No new delivery was created') && !duplicate.includes('File #') && !duplicate.includes('READY_TO_PROCESS'))
const upload=render(UploadFileDialog,{open:true,workspaceId:'10',datasetId:'20',datasetName:'Customers',onClose:()=>{},onAccepted:()=>{}})
for (const [state,configuring,inspected,inspecting,expected] of [['IDLE',false,false,false,0],['IDLE',false,false,true,1],['IDLE',false,true,false,2],['IDLE',true,true,false,3],['UPLOADING',false,true,false,4],['FAILED',false,true,false,4],['SUCCESS',false,true,false,5]]) assert.equal(uploadStep(state,configuring,inspected,inspecting),expected)
for(const label of ['Delivery steps','Choose File','Inspect','Check Compatibility','Configure Contract','Upload','Ready to Process','aria-current="step"']) assert.ok(upload.includes(label))
assert.ok(render(NotFoundPage).includes('Page not found') && render(NotFoundPage).includes('Back to Workspaces'))
result={error:'Missing dataset'}
const invalid=render(DatasetDetailPage,{},undefined,{workspace:{name:'Sales'}})
assert.ok(invalid.includes('Dataset not found or unavailable') && invalid.includes('Back to Workspace') && !invalid.includes('customers.csv'))
const { getDataset }=require('../src/services/api/datasets.ts')
global.fetch=async()=>new Response(JSON.stringify({dataset_id:20,workspace_id:99,dataset_name:'Wrong scope',status:'ACTIVE'}))
getDataset('10','20').then(()=>{throw new Error('scope mismatch accepted')}).catch(error=>{assert.match(error.message,/another workspace/);console.log('Phase 5C statuses, workspace states, overview guidance, authoritative Files, contract setup/summary/edit, Rules reuse/review, upload steps, duplicates, quality/readiness, unavailable archival and scope checks passed.')})
