const assert=require('node:assert/strict'),React=require('react'),fs=require('node:fs'),ts=require('typescript')
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
const {MemoryRouter}=require('react-router-dom'),{renderToStaticMarkup}=require('react-dom/server')
const {WorkspaceEvidence}=require('../src/features/workspaces/understanding/WorkspaceEvidence.tsx')
const api=require('../src/services/api/workspaceUnderstanding.ts')
const base={workspace_id:1,workspace_name:'Commerce',status:'NOT_GENERATED',can_generate:true,readiness_message:'All datasets are eligible.',coverage:{datasets_total:2,datasets_analyzed:2,partial:false,excluded:[]},source_pins:[{dataset_id:1,dataset_name:'Orders'},{dataset_id:2,dataset_name:'Customers'}],suggestion:null}
const render=(extra={})=>renderToStaticMarkup(React.createElement(MemoryRouter,null,React.createElement(WorkspaceEvidence,{data:{...base,...extra},busy:false,error:'',generate:()=>{}})))
assert.ok(render().includes('Generate Workspace Understanding'))
assert.ok(render({status:'GENERATING',can_generate:false}).includes('Analyzing…'))
assert.ok(render({status:'GENERATING',can_generate:false}).includes('disabled=""'))
assert.ok(render({status:'FAILED',failure_code:'TIMEOUT'}).includes('Retry Understanding'))
assert.ok(render({coverage:{...base.coverage,datasets_analyzed:1,partial:true,excluded:[{dataset_id:3,dataset_name:'Missing',reason:'PROFILE_STALE'}]}}).includes('Review Dataset'))
const classification={primary:{label:'Retail',confidence:.9,rationale:'Commerce columns'},alternatives:[{label:'Operations',confidence:.5,rationale:'Possible'}]}
const suggestion={suggestion_version:1,completed_at:'2026-10-09T00:00:00Z',reasoning:{domain:classification,subdomain:classification,overall_confidence:.8,business_processes:[{name:'Order handling',contributing_datasets:[1,2],confidence:.8,rationale:'Participation'}],entities:[{canonical_name:'Customer',contributing_datasets:[2],confidence:.8,rationale:'Likely entity'}],dataset_roles:[{dataset_id:1,role:'TRANSACTION',confidence:.8,rationale:'Orders'}],warnings:['Mixed domain possible'],unresolved_questions:['Confirm business scope']}}
let html=render({status:'READY',suggestion})
for(const v of ['Retail','Order handling','Customer','TRANSACTION','Confirm business scope','AI suggested','Review required','Alternative interpretations'])assert.ok(html.includes(v),v)
html=render({status:'STALE',suggestion});assert.ok(html.includes('Earlier suggestions are hidden') && !html.includes('Retail'))
html=render({status:'READY',suggestion:{...suggestion,reasoning:{...suggestion.reasoning,overall_confidence:.3,domain:{primary:{label:null,confidence:.3,rationale:'Mixed'},alternatives:[]}}}})
assert.ok(html.includes('Uncertain') && html.includes('multiple business domains'))
const component=fs.readFileSync('src/features/workspaces/understanding/WorkspaceUnderstanding.tsx','utf8')
assert.ok(component.includes('key={workspaceId}') && component.includes('controller.abort()') && component.includes('if(reading)return') && component.includes('if(action.current)return'))
assert.ok(fs.readFileSync('src/features/workspaces/understanding/WorkspaceEvidence.tsx','utf8').includes('min-w-0'))
async function main(){
 let call;global.fetch=async(url,options)=>{call={url,options};return {ok:true,json:async()=>base}}
 await api.generateWorkspaceUnderstanding('1');assert.ok(call.url.endsWith('/workspaces/1/semantic-understanding/generate'));assert.equal(call.options.method,'POST');assert.ok(!call.options.body)
 await api.getWorkspaceReadiness('1');assert.ok(call.url.endsWith('/readiness'))
 global.fetch=async()=>({ok:true,json:async()=>({...base,workspace_id:2})})
 await assert.rejects(api.getWorkspaceUnderstanding('1'),/another workspace/)
 console.log('Workspace lifecycle, coverage, domain, entities, processes, roles, uncertainty, scoping and responsive structure passed')
}
main().catch(e=>{console.error(e);process.exitCode=1})
