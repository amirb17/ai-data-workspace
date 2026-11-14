// Real API client and state selection; synthetic HTTP responses, no cloud calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript')
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
const {nextStage,submitStage,readStageJobs}=require('../src/services/api/processingJobs.ts')
const {datasetProcessing}=require('../src/services/api/rules.ts')
const context={workspace_id:10,dataset_id:20,upload_request_id:40,status:'READY_TO_PROCESS',rule_version:null,rule_state:null,can_continue:false}
async function main(){
 const calls=[]
 global.fetch=async(url,options={})=>{calls.push({url,...options});return new Response(JSON.stringify(url.endsWith('/product/jobs')?{workspace_id:10,dataset_id:20,jobs:[]}:url.endsWith('/jobs')?{job_id:'new-job'}:{workspace_id:10,dataset_id:20,deliveries:[{context}],summary:{pending:1}}))}
 assert.equal(nextStage(context),'BRONZE')
 for(const status of ['AWAITING_RULES','BRONZE_PROCESSING','SILVER_PROCESSING','GOLD_PROCESSING','SUCCESS','SUCCESS_WITH_WARNINGS','DATASET_UPDATE_PROCESSING'])assert.equal(nextStage({...context,status}),null,status)
 assert.equal(nextStage({...context,archived_at:'2026-11-14'}),null)
 assert.equal(nextStage({...context,status:'READY_FOR_SILVER'}),null,'rule approval is required')
 const approved={...context,rule_version:1,rule_state:'FINALIZED',can_continue:true}
 for(const status of ['READY_FOR_SILVER','SILVER_FAILED','READY_TO_APPLY','DATASET_UPDATE_FAILED'])assert.equal(nextStage({...approved,status}),'SILVER')
 for(const status of ['READY_FOR_GOLD','GOLD_FAILED'])assert.equal(nextStage({...approved,status}),'GOLD')
 await submitStage('10','20',context,[])
 await submitStage('10','20',context,[])
 assert.equal(calls[0].body,calls[1].body,'lost responses and repeated clicks reuse the admission key')
 assert.equal(JSON.parse(calls[0].body).operation,'BRONZE')
 for(const status of ['QUEUED','RUNNING','RETRY_WAIT']){
  const before=calls.length
  await submitStage('10','20',context,[{upload_id:40,operation:'BRONZE',status,job_id:'active'}])
  assert.equal(calls.length,before,'active job suppresses duplicate admission')
 }
 await submitStage('10','20',context,[{upload_id:40,operation:'BRONZE',status:'FAILED',job_id:'failed-job'}])
 const retry=calls.at(-1).body
 assert.notEqual(retry,calls[0].body)
 await submitStage('10','20',context,[{upload_id:40,operation:'BRONZE',status:'FAILED',job_id:'failed-job'}])
 assert.equal(calls.at(-1).body,retry,'retry is itself idempotent')
 await submitStage('10','20',{...approved,status:'READY_TO_APPLY',load_policy:{policy_id:1}},[])
 assert.equal(JSON.parse(calls.at(-1).body).operation,'SILVER','dataset acceptance reuses Silver operation')
 const firstPolicyKey=JSON.parse(calls.at(-1).body).idempotency_key
 await submitStage('10','20',{...approved,status:'READY_TO_APPLY',load_policy:{policy_id:2}},[])
 assert.notEqual(JSON.parse(calls.at(-1).body).idempotency_key,firstPolicyKey,'a new pinned policy has a distinct stage admission')
 const count=calls.length
 await assert.rejects(submitStage('11','20',context,[]),/scope mismatch/)
 assert.equal(calls.length,count,'scope mismatch makes no HTTP call')
 await datasetProcessing('10','20',false)
 assert.equal(calls.at(-1).method,undefined,'status reads never start jobs')
 await datasetProcessing('10','20',true)
 assert.ok(calls.at(-1).url.endsWith('/files/uploads/40/jobs'))
 assert.ok(calls.every(c=>!c.url.includes('/pending')&&!c.url.includes('/continue?')&&!c.url.includes('/process?')))
 global.fetch=async()=>new Response(JSON.stringify({workspace_id:11,dataset_id:20,jobs:[]}))
 await assert.rejects(readStageJobs('10','20'),/scope mismatch/)
 global.fetch=async()=>new Response(JSON.stringify({detail:'private-secret-location'}),{status:503})
 await assert.rejects(submitStage('10','20',context,[]),e=>e.status===503&&!e.message.includes('private-secret'))
 console.log('Async stage selection, rule pause, idempotency, retries, active jobs, scope and safe failures passed (synthetic HTTP).')
}
main().catch(error=>{console.error(error);process.exitCode=1})
