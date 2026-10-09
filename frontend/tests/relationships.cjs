const assert=require('node:assert/strict'),React=require('react'),fs=require('node:fs'),ts=require('typescript')
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
const {MemoryRouter}=require('react-router-dom'),{renderToStaticMarkup}=require('react-dom/server')
const {RelationshipList}=require('../src/features/workspaces/relationships/RelationshipsPage.tsx')
const api=require('../src/services/api/relationships.ts')
const stats={rows:3,non_null_rows:3,distinct_keys:2,duplicate_rows:1,null_ratio:0,uniqueness_ratio:2/3}
const e={workspace_id:1,candidate_key:'stable',parent:{dataset_id:1,dataset_name:'Customers',columns:['customer_id']},child:{dataset_id:2,dataset_name:'Orders',columns:['customer_id']},candidate_cardinality:'ONE_TO_MANY',deterministic_score:100,can_confirm:true,warnings:['Optionality requires review'],source_pins:[{dataset_id:1,state_version:2,profile_version:2}],score_components:[{name:'name',points:20,explanation:'Normalized equality'}],signals:{parent_type:'TEXT',child_type:'TEXT',configured_parent_key:true,parent:{...stats,distinct_keys:3,duplicate_rows:0,uniqueness_ratio:1},child:stats,overlap:{method:'EXACT_BOUNDED',child_non_null_distinct_keys:2,matched_distinct_keys:2,missing_distinct_keys:0,child_to_parent_coverage:1,parent_referenced_ratio:2/3}}}
const candidate={candidate_id:8,run_id:4,evidence:e,review_status:'REVIEW_REQUIRED',review_version:0}
const base={workspace_id:1,status:'NOT_GENERATED',can_discover:true,readiness_message:'Ready profiles',coverage:{datasets_total:2,datasets_analyzed:2,partial:false,excluded:[]},semantic_context:{workspace_semantics:'STALE',workspace_domain:null,dataset_semantics_available:0},candidates:[],reviewed_relationships:[]}
const render=(change={},busy=false)=>renderToStaticMarkup(React.createElement(MemoryRouter,null,React.createElement(RelationshipList,{data:{...base,...change},busy,generate:()=>{},review:()=>{}})))
assert.ok(render().includes('Discover Relationships'))
assert.ok(render({status:'DISCOVERING',can_discover:false}).includes('Verifying…'))
assert.ok(render({status:'FAILED'}).includes('Retry Discovery'))
let html=render({status:'READY',candidates:[candidate]})
for(const word of ['Customers','Orders','Confirm','Reject','Non-null child distinct-key coverage','Parent keys referenced','not a probability','Review required','No new AI request'])assert.ok(html.includes(word),word)
html=render({status:'READY',candidates:[{...candidate,evidence:{...e,can_confirm:false}}]});assert.ok(html.includes('Confirmation requires'))
html=render({status:'STALE',reviewed_relationships:[{candidate_id:8,relationship_version:1,status:'CONFIRMED',reviewed_at:'2026-10-09T00:00:00Z',evidence:e,structural_status:'CURRENT',verification_status:'STALE'}]})
assert.ok(html.includes('Earlier candidates are hidden') && html.includes('Historical coverage shown for audit'))
assert.ok(render({coverage:{...base.coverage,partial:true,datasets_total:3,excluded:[{dataset_id:3,dataset_name:'Missing',reason:'PROFILE_STALE'}]}}).includes('Review Dataset'))
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/features/workspaces/relationships/RelationshipsPage.tsx'),'utf8')
for(const token of ['key={workspaceId}','controller.abort()','if(reading)return','if(action.current)return','data.review_revision','data.source_pins'])assert.ok(source.includes(token),token)
assert.ok(fs.readFileSync(require('node:path').join(__dirname,'../src/features/workspaces/relationships/RelationshipEvidenceCard.tsx'),'utf8').includes('min-w-0'))
async function main(){
 let call;global.fetch=async(url,options)=>{call={url,options};return {ok:true,json:async()=>base}}
 await api.discoverRelationships('1');assert.ok(call.url.endsWith('/workspaces/1/relationships/discover'));assert.ok(!call.options.body)
 await api.reviewRelationship('1',candidate,'confirm');assert.ok(call.url.endsWith('/candidates/8/confirm'));assert.deepEqual(JSON.parse(call.options.body),{expected_review_version:0})
 global.fetch=async()=>({ok:true,json:async()=>({...base,workspace_id:2})})
 await assert.rejects(api.getRelationships('1'),/another workspace/)
 console.log('Relationship lifecycle, evidence, reviews, freshness, API scope and responsive structure passed')
}
main().catch(e=>{console.error(e);process.exitCode=1})
