// Existing lightweight TypeScript + SSR harness; no framework/dependencies.
const assert = require('node:assert/strict')
const fs = require('node:fs'), ts = require('typescript'), React = require('react')
const {renderToStaticMarkup} = require('react-dom/server')
for (const ext of ['.ts','.tsx']) require.extensions[ext] = (module,filename) => module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename)
const resource = require('../src/services/api/useApiResource.ts')
const foundation={workspace_id:10,dataset_id:20,execution_available:false,schema_versions:[],policies:[],applications:[],current_state:null}
let data=foundation
resource.useApiResource=(key) => {assert.equal(key,'incremental:10:20');return {data,retry:()=>{}}}
const {IncrementalPolicy} = require('../src/features/datasets/contracts/components/IncrementalPolicy.tsx')
const {ApplicationMetrics} = require('../src/features/ingestion/components/ApplicationMetrics.tsx')
const {getIncrementalFoundation,saveLoadPolicy} = require('../src/services/api/incremental.ts')
const render=props=>renderToStaticMarkup(React.createElement(IncrementalPolicy,{workspaceId:'10',datasetId:'20',...props}))
let html=render({browserContract:{loadMode:'APPEND',primaryKey:['id'],schemaEvolutionPolicy:'STRICT'}})
assert.ok(html.includes('Not configured') && html.includes('Configure backend policy'))
assert.ok(!html.includes('APPEND · Policy')) // Browser defaults never authoritative.
assert.ok(html.includes('APPEND updates the trusted dataset') && html.includes('Current trusted records'))
data={...foundation,policies:[{policy_id:1,dataset_id:20,dataset_version_id:7,policy_version:1,load_strategy:'UPSERT',business_keys:['order_id','line'],schema_evolution_policy:'STRICT'}]}
html=render({})
assert.ok(html.includes('UPSERT · Policy 1') && html.includes('order_id + line') && html.includes('updated when their values change'))
assert.ok(html.includes('Latest applied delivery wins') && html.includes('These columns identify one logical record'))
data={...data,policies:[{...data.policies[0],event_time_column:'updated_at'}]}
html=render({})
assert.ok(html.includes('Change ordering') && html.includes('updated_at') && html.includes('Older late-arriving records will not overwrite newer data'))
const {businessKeyError}=require('../src/features/datasets/contracts/policyValidation.ts')
assert.ok(businessKeyError('UPSERT','',['id']))
assert.ok(businessKeyError('UPSERT','missing',['id']))
assert.ok(businessKeyError('UPSERT','id,id',['id']))
assert.equal(businessKeyError('UPSERT','id, line',['id','line']),'')
data={...data,current_state:{state_id:5,row_count:123,published_at:'2026-10-06',state_analytics_status:'STALE'}}
html=render({})
assert.ok(html.includes('123') && html.includes('requires an explicit migration') && html.includes('disabled=""'))
assert.ok(html.includes('Analytics refresh required'))
assert.ok(!render({summaryOnly:true}).includes('Review policy change'))
data={...foundation,policies:[{policy_id:1,dataset_id:20,dataset_version_id:7,policy_version:1,load_strategy:'APPEND',business_keys:[],schema_evolution_policy:'STRICT'}],current_state:{state_id:5,row_count:150,published_at:'2026-10-06'},applications:[{application_id:2,upload_request_id:99,status:'SUCCESS',source_file_name:'delivery_2.csv',inserted_rows:50,duplicate_rows:0,rejected_rows:0,incremental_rejected_rows:0}]}
html=render({summaryOnly:true})
assert.ok(html.includes('150') && html.includes('delivery_2.csv') && html.includes('Added 50'))
assert.ok(html.includes('without a key') && html.includes('cannot identify the same business record'))
data={...data,policies:[{...data.policies[0],business_keys:['id']}]}
html=render({})
assert.ok(html.includes('Event / record key: id') && html.includes('already been received'))
data={...data,policies:[data.policies[0],{...data.policies[0],policy_id:2,policy_version:2,load_strategy:'UPSERT'}],
  current_state:{...data.current_state,policy_id:1},applications:[{...data.applications[0],result_state_id:5,source_file_name:'actual-head.csv'},
  {...data.applications[0],application_id:3,result_state_id:4,source_file_name:'older-publication.csv'}]}
html=render({summaryOnly:true})
assert.ok(html.includes('APPEND · Policy 1') && html.includes('Latest applied delivery: actual-head.csv'))
const metrics={input_rows:3,valid_rows:2,rejected_rows:1,inserted_rows:null,updated_rows:null,unchanged_rows:null,duplicate_rows:null,deactivated_rows:null,current_state_rows:null}
const metricHtml=renderToStaticMarkup(React.createElement(ApplicationMetrics,{metrics}))
assert.equal((metricHtml.match(/<dd>—<\/dd>/g)||[]).length,6)
assert.ok(metricHtml.includes('Current Dataset Rows') && !metricHtml.includes('<dd>0</dd>'))
async function main(){
  global.fetch=async()=>({ok:true,json:async()=>foundation})
  assert.deepEqual(await getIncrementalFoundation('10','20'),foundation)
  global.fetch=async()=>({ok:true,json:async()=>({...foundation,dataset_id:21})})
  await assert.rejects(getIncrementalFoundation('10','20'),/another dataset/)
  global.fetch=async()=>({ok:true,json:async()=>({...foundation,workspace_id:11})})
  await assert.rejects(getIncrementalFoundation('10','20'),/another dataset/)
  let sent
  global.fetch=async(url,options)=>{sent={url,body:JSON.parse(options.body)};return {ok:true,json:async()=>({dataset_id:20,dataset_version_id:7,policy_id:9})}}
  const input={dataset_version_id:7,expected_policy_version:0,load_strategy:'APPEND',business_keys:[],schema_evolution_policy:'STRICT',event_time_column:null,confirm_policy_change:true}
  await saveLoadPolicy('10','20',input)
  assert.ok(sent.url.endsWith('/workspaces/10/datasets/20/incremental/policies')); assert.deepEqual(sent.body,input)
  global.fetch=async()=>({ok:false,status:409,json:async()=>({detail:'private/path'})})
  await assert.rejects(saveLoadPolicy('10','20',input),e=>e.message.includes('migration')&&!e.message.includes('private/path'))
  console.log('Incremental policy ownership, explicit backend bridge, readiness and null metrics checks passed')
}
main().catch(error=>{console.error(error);process.exitCode=1})
