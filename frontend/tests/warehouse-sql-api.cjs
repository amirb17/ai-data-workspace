const assert=require('node:assert/strict')
const api=require('../src/services/api/warehouseQueries.ts')
const {setIdentityMode}=require('../src/services/api/client.ts')
setIdentityMode('session')
let body,received=[],code=200
const handle='a'.repeat(32)
global.fetch=async(url,options={})=>{received.push({url,options});return {ok:code===200,status:code,json:async()=>url.endsWith('/auth/csrf')?{csrf_token:'synthetic-csrf'}:body}}
async function main(){
 let checks=0
 body={valid:true,model_id:10,model_version_id:11,columns:['id'],row_limit:100,estimated_bytes:10485760,budget_decision:'ALLOWED',reason:'WITHIN_BUDGET',query_execution_enabled:false}
 const validation={...body};assert.equal((await api.validateWarehouseQuery('1',10,'SELECT id FROM model_10 LIMIT 100')).estimated_bytes,10485760);checks++
 for(const patch of [{model_id:20},{row_limit:501},{estimated_bytes:-1},{budget_decision:'APPROVED'},{query_execution_enabled:true},{columns:['s3://private']}]){body={...validation,...patch};await assert.rejects(()=>api.validateWarehouseQuery('1',10,'text'))}checks++
 body={attempt_handle:handle,reused:true,query_execution_enabled:false};assert.equal((await api.admitWarehouseQuery('1',10,'original SQL','original-key-000001')).attempt_handle,handle);const request=received.at(-1);assert.ok(request.url.endsWith('/workspaces/1/warehouse/queries'));assert.deepEqual(JSON.parse(request.options.body),{model_id:10,sql:'original SQL',idempotency_key:'original-key-000001'});assert.equal(request.options.credentials,'include');checks++
 const status={attempt_handle:handle,status:'SUCCEEDED',results_available:true,query_execution_enabled:false};body=status;assert.equal((await api.warehouseQueryStatus('1',handle)).status,'SUCCEEDED');for(const patch of [{attempt_handle:'b'.repeat(32)},{status:'TIMED_OUT'},{results_available:false},{query_execution_enabled:true}]){body={...status,...patch};await assert.rejects(()=>api.warehouseQueryStatus('1',handle))}checks++
 const page={attempt_handle:handle,columns:['id'],rows:[['9007199254740993.01']],has_more:false,bytes_encoded:100,query_execution_enabled:false};body=page;assert.equal((await api.warehouseQueryResults('1',handle)).rows[0][0],'9007199254740993.01');checks++
 for(const patch of [{rows:[[9007199254740992]]},{rows:[[{}]]},{rows:[[1,2]]},{rows:Array(101).fill([1])},{bytes_encoded:262145},{has_more:'yes'},{attempt_handle:'b'.repeat(32)}]){body={...page,...patch};await assert.rejects(()=>api.warehouseQueryResults('1',handle))}checks++
 const before=received.length;await assert.rejects(()=>api.warehouseQueryStatus('2','bad-handle'));await assert.rejects(()=>api.warehouseQueryStatus('../other',handle));assert.equal(received.length,before);checks++
 code=503;body={detail:'private-provider.internal-table'};await assert.rejects(()=>api.validateWarehouseQuery('1',10,'sql'),e=>e.status===503&&!e.message.includes('internal-table'));checks++
 console.log(`${checks} Warehouse SQL API contract scenarios passed`)
}
main().catch(error=>{console.error(error);process.exitCode=1})
