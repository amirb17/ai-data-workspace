const assert=require('node:assert/strict'),React=require('react'),fs=require('node:fs'),ts=require('typescript')
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
const {MemoryRouter}=require('react-router-dom'),{renderToStaticMarkup}=require('react-dom/server')
const {MetricList}=require('../src/features/workspaces/metrics/MetricsPage.tsx'),api=require('../src/services/api/metrics.ts')
const definition={name:'Record count',description:'Base records',base_dataset_id:1,base_entity:'Record',grain:'RECORD',grain_keys:[],metric_type:'COUNT',confidence:.95,rationale:'Canonical technical definition',unit:'NUMBER',formula:{root:'count',scale:1,zero_denominator:'NULL',nodes:[{id:'count',op:'COUNT',args:[],column:null,filters:[]}]},dimensions:[],relationship_ids:[],time_field:null,time_bucket:null,default_filters:[]}
const candidate={candidate_id:8,run_id:4,definition,validation_status:'VALID',decision:'AUTO_ACCEPT',errors:[],review_reasons:[],definition_freshness:'CURRENT',dependency_freshness:'CURRENT',data_freshness:'CURRENT',review_status:'AUTO_ACCEPTED',review_version:0,can_approve:false,can_reject:true,required_columns:[]}
const base={workspace_id:1,status:'NOT_GENERATED',can_discover:true,readiness_message:'Current understanding',coverage:{datasets_total:2,datasets_analyzed:2,excluded:[]},candidates:[]}
const render=(change={})=>renderToStaticMarkup(React.createElement(MemoryRouter,null,React.createElement(MetricList,{data:{...base,...change},busy:false,generate:()=>{},review:()=>{}})))
assert.ok(render().includes('Discover Metrics'))
assert.ok(render({status:'FAILED',failure_code:'TIMEOUT'}).includes('Retry Discovery'))
assert.ok(render({status:'READY',candidates:[]}).includes('No safe metric'))
let html=render({status:'READY',candidates:[candidate]})
assert.ok(html.includes('Accepted by deterministic policy') && !html.includes('Approve definition'))
assert.ok(html.includes('uncalibrated') && html.includes('no value has been calculated'))
html=render({status:'READY',candidates:[{...candidate,decision:'REVIEW_RECOMMENDED',review_status:'UNREVIEWED',can_approve:true}]})
assert.ok(html.includes('Review is optional') && html.includes('Approve definition (optional)'))
html=render({status:'READY',candidates:[{...candidate,decision:'REVIEW_REQUIRED',validation_status:'REVIEW_REQUIRED',review_reasons:['Ambiguous category'],can_approve:false,definition:{...definition,default_filters:[{column:{dataset_id:1,column:'status'},operator:'EQ',category_ref:'unresolved_success'}]}}]})
assert.ok(html.includes('Ambiguous category') && html.includes('unresolved_success') && !html.includes('Approve definition'))
assert.ok(render({status:'STALE'}).includes('Historical definitions'))
assert.ok(render({coverage:{...base.coverage,excluded:[{dataset_id:3,dataset_name:'Excluded',reason:'PROFILE_STALE'}]}}).includes('/app/workspaces/1/datasets/3/understanding'))
const code=fs.readFileSync(require('node:path').join(__dirname,'../src/features/workspaces/metrics/MetricsPage.tsx'),'utf8')
for(const token of ['key={workspaceId}','controller.abort()','if(reading)return','if(action.current)return','data.review_revision','data.freshness_revision'])assert.ok(code.includes(token),token)
async function main(){
 let call;global.fetch=async(url,options)=>{call={url,options};return {ok:true,json:async()=>base}}
 await api.discoverMetrics('1');assert.ok(call.url.endsWith('/workspaces/1/metrics/discover') && !call.options.body)
 await api.reviewMetric('1',candidate,'reject');assert.ok(call.url.endsWith('/candidates/8/reject'));assert.deepEqual(JSON.parse(call.options.body),{expected_review_version:0})
 global.fetch=async()=>({ok:true,json:async()=>({...base,workspace_id:2})})
 await assert.rejects(api.getMetrics('1'),/another workspace/)
 console.log('Metric decision classes, exact definitions, freshness, scoped API and navigation guards passed')
}
main().catch(e=>{console.error(e);process.exitCode=1})
