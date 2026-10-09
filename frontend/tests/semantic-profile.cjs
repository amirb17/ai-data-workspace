require('./incremental.cjs')
const assert=require('node:assert/strict'),React=require('react'),fs=require('node:fs')
const {renderToStaticMarkup}=require('react-dom/server')
const {ProfileEvidence}=require('../src/features/datasets/profile/DataProfile.tsx')
const api=require('../src/services/api/profile.ts')
const base={workspace_id:10,dataset_id:20,dataset_name:'Synthetic',freshness:'NOT_PROFILED',profile_ready:false,current_state_id:null,profile_timestamp:null,can_refresh:false,columns:[],summary:null}
const render=(extra={})=>renderToStaticMarkup(React.createElement(ProfileEvidence,{data:{...base,...extra},refresh:()=>{}}))
assert.ok(render().includes('Complete a dataset update') && !render().includes('<button'))
assert.ok(render({freshness:'STALE',current_state_id:1,can_refresh:true}).includes('Dataset changed — refresh profile'))
assert.ok(render({freshness:'PROFILING',current_state_id:1}).includes('disabled=""'))
assert.ok(render({freshness:'FAILED',current_state_id:1,can_refresh:true}).includes('Retry Profile'))
const column={original_name:'customerId',canonical_type:'TEXT',distinct_ratio:1,null_ratio:0,null_count:0,distinct_count:2,authoritative_business_key:true,business_key_position:1,authoritative_event_time:true,approved_required:true,identifier_candidate:true,min_value:null,numeric_statistics:null,datetime_statistics:null,pattern_hints:[],categorical_statistics:null,sensitivity_hints:[{hint:'email_like',confidence:'PATTERN_ONLY'}]}
const summary={row_count:2,column_count:1,identifier_candidates:['customerId'],datetime_candidates:[],numeric_columns:[],potential_sensitive_fields:['customerId'],load_strategy:'SNAPSHOT',business_key:['customerId'],event_time_column:'event',active_rows:2,inactive_rows:1}
let html=render({freshness:'READY',profile_ready:true,summary,columns:[column],state_version:2,schema_version:1,profile_version:1})
for(const text of ['Profile ready','100%','Potential identifier','Potential sensitive field','Configured business key','Configured event time','Approved required field','active records only'])assert.ok(html.includes(text),text)
for(const text of ['Contains PII','Revenue','Healthcare industry','Primary key','Sales domain'])assert.ok(!html.includes(text))
html=render({freshness:'STALE',summary,columns:[column]})
assert.ok(!html.includes('customerId') && !html.includes('100%'))
const source=fs.readFileSync('src/features/datasets/profile/DataProfile.tsx','utf8')
assert.ok(source.includes('key={`${workspaceId}:${datasetId}`}') && source.includes('controller.abort()') && source.includes('accepting.current'))
assert.ok(fs.readFileSync('src/features/datasets/profile/ColumnEvidenceCard.tsx','utf8').includes('min-w-0'))
async function main(){
 let call
 global.fetch=async(url,options)=>{call={url,options};return {ok:true,json:async()=>base}}
 await api.refreshDatasetProfile('10','20');assert.ok(call.url.endsWith('/workspaces/10/datasets/20/profile/refresh'))
 assert.equal(call.options.method,'POST');assert.ok(!call.options.body)
 global.fetch=async()=>({ok:true,json:async()=>({...base,workspace_id:11})})
 await assert.rejects(api.getDatasetProfile('10','20'),/another dataset/)
 console.log('Semantic profile freshness, factual wording, redaction, context isolation and responsive structure passed')
}
main().catch(e=>{console.error(e);process.exitCode=1})
