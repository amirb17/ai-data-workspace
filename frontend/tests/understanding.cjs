const assert=require('node:assert/strict'), React=require('react'),fs=require('node:fs'),ts=require('typescript')
// Reuse the lightweight TS/SSR pattern without executing another suite's async fetch mocks.
for (const ext of ['.ts','.tsx']) require.extensions[ext]=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename)
const {MemoryRouter}=require('react-router-dom'),{renderToStaticMarkup}=require('react-dom/server')
const {UnderstandingEvidence}=require('../src/features/datasets/understanding/UnderstandingEvidence.tsx')
const api=require('../src/services/api/understanding.ts')
const base={workspace_id:15,dataset_id:23,dataset_name:'Synthetic',status:'NOT_GENERATED',profile_freshness:'READY',can_generate:true,suggestion:null,evidence:[]}
const render=(extra={})=>renderToStaticMarkup(React.createElement(MemoryRouter,null,React.createElement(UnderstandingEvidence,{data:{...base,...extra},generate:()=>{}})))
assert.ok(render().includes('has not been generated') && render().includes('Analyze Dataset'))
assert.ok(render({status:'GENERATING',can_generate:false}).includes('disabled=""'))
assert.ok(render({status:'FAILED'}).includes('Retry Analysis'))
assert.ok(render({status:'STALE'}).includes('Previous suggestions are stale'))
assert.ok(render({profile_freshness:'STALE'}).includes('Go to Data Profile') && !render({profile_freshness:'STALE'}).includes('<button'))
const classification={primary:{label:'Commerce',confidence:.85,rationale:'Names suggest commerce.'},alternatives:[{label:'Operations',confidence:.5,rationale:'Possible'}]}
const suggestion={suggestion_version:1,source_profile_id:4,source_state_version:3,provider:'fake',model:'test',completed_at:'2026-10-09T00:00:00Z',compact_mode:true,
 reasoning:{domain:classification,subdomain:classification,entity:{...classification,primary:{...classification.primary,label:'Orders'}},overall_confidence:.8,warnings:['Review required'],unresolved_questions:['What units?'],columns:[{column_name:'order_id',suggested_role:'IDENTIFIER',business_meaning:'Possible order identifier',confidence:.9,rationale:'Configured key',warnings:[]}]}}
const evidence=[{original_name:'order_id',canonical_type:'TEXT',distinct_ratio:1,null_ratio:0,authoritative_business_key:true,business_key_position:1,authoritative_event_time:false,sensitivity_hints:[{hint:'potential'}]}]
let html=render({status:'READY',suggestion,evidence})
for(const value of ['Commerce','Orders','Possible alternatives','Operations','IDENTIFIER','Deterministic evidence','AI suggestion','Configured business key','Potential sensitive field','Review required','context was reduced'])assert.ok(html.includes(value),value)
assert.ok(!html.includes('Confirmed PII') && !html.includes('Analyze Dataset'))
html=render({status:'STALE',suggestion,evidence});assert.ok(!html.includes('Commerce') && !html.includes('Possible order identifier'))
html=render({status:'READY',suggestion:{...suggestion,reasoning:{...suggestion.reasoning,domain:{primary:{label:null,confidence:.2,rationale:'Unclear'},alternatives:[]}}},evidence})
assert.ok(html.includes('couldn&#x27;t confidently determine') && html.includes('Low'))
const source=fs.readFileSync('src/features/datasets/understanding/DatasetUnderstanding.tsx','utf8')
assert.ok(source.includes('key={`${workspaceId}:${datasetId}`}') && source.includes('controller.abort()') && source.includes('active.current'))
assert.ok(fs.readFileSync('src/features/datasets/understanding/UnderstandingEvidence.tsx','utf8').includes('min-w-0'))
async function main(){
 let call
 global.fetch=async(url,options)=>{call={url,options};return {ok:true,json:async()=>base}}
 await api.generateUnderstanding('15','23');assert.ok(call.url.endsWith('/workspaces/15/datasets/23/semantic-understanding/generate'))
 assert.equal(call.options.method,'POST');assert.ok(!call.options.body)
 await api.getUnderstandingReadiness('15','23');assert.ok(call.url.endsWith('/semantic-understanding/readiness'))
 global.fetch=async()=>({ok:true,json:async()=>({...base,dataset_id:24})})
 await assert.rejects(api.getUnderstanding('15','23'),/another dataset/)
 console.log('AI Understanding lifecycle, suggestions, uncertainty, evidence distinction, redaction, scoped API and responsive structure passed')
}
main().catch(error=>{console.error(error);process.exitCode=1})
