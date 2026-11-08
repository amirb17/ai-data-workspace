// Real React interactions with synthetic API replies; no storage or AWS calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript')
require('./preserve-evidence.cjs')
async function main(){
 const fixture=require('node:path').resolve(__dirname,'../phase8b-fixture-preview.html')
 if(fs.existsSync(fixture)){
  const before=fs.readFileSync(fixture),mtime=fs.statSync(fixture).mtimeMs
  fs.writeFileSync(fixture,before)
  assert.equal(fs.statSync(fixture).mtimeMs,mtime)
  fs.writeFileSync(fixture,'synthetic guard test output')
  assert.deepEqual(fs.readFileSync(fixture),before)
  assert.equal(fs.statSync(fixture).mtimeMs,mtime)
 }
 const {Window}=await import('happy-dom'),window=new Window()
 for(const name of ['window','document','HTMLElement','HTMLInputElement','HTMLSelectElement','Element','Node','Event','MouseEvent','navigator'])Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.IS_REACT_ACT_ENVIRONMENT=true
 for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
 const React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),{MemoryRouter}=require('react-router-dom')
 const {ProductReadiness}=require('../src/features/datasets/readiness/ProductReadiness.tsx')
 const api=require('../src/services/api/product.ts'),client=require('../src/services/api/client.ts'),{resolveApiBase}=require('../src/services/api/apiOrigin.ts')
 client.setIdentityMode('session')
 assert.equal(resolveApiBase(undefined,true),'')
 assert.equal(resolveApiBase('/api',true),'/api')
 for(const bad of ['http://localhost:8000','//evil.example','/api/../private','/%2e%2e/api','https://user:password@example.com','https://api.example/private?key=x'])assert.throws(()=>resolveApiBase(bad,true))
 const Module=require('node:module'),path=require('node:path'),filename=path.resolve(__dirname,'../src/services/api/client.ts')
 const unsafeClient=new Module(filename,module);unsafeClient.filename=filename;unsafeClient.paths=module.paths
 unsafeClient._compile(ts.transpileModule(fs.readFileSync(filename,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000",PROD:true})'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,filename)
 global.fetch=()=>assert.fail('Unsafe production configuration performed a request')
 await assert.rejects(unsafeClient.exports.request('/auth/config'),/not configured safely/)
 await assert.rejects(api.csvBytes({name:'orders.xlsx',size:3,arrayBuffer:async()=>new ArrayBuffer(3)}))
 await assert.rejects(api.csvBytes({name:'orders.csv',size:2097153,arrayBuffer:async()=>assert.fail('oversized file read')}))
 const calls=[];let reply='quality',failCapabilities=false,defer
 global.fetch=async(url,options={})=>{
  calls.push({url,options})
  if(url.endsWith('/auth/csrf'))return {ok:true,status:200,json:async()=>({csrf_token:'synthetic-csrf'})}
  const ids=url.match(/workspaces\/(\d+)\/datasets\/(\d+)/);assert.ok(ids)
  const scope={workspace_id:Number(ids[1]),dataset_id:Number(ids[2])}
  let value={...scope}
  if(url.endsWith('/capabilities')){
   if(failCapabilities)return {ok:false,status:401,json:async()=>({detail:'SYNTHETIC_PRIVATE'})}
   value={...scope,csv_upload:true,xlsx:'UNSUPPORTED',max_preview_bytes:2097152,processing_submission:'DISABLED',warehouse:'NOT_CONFIGURED',warehouse_models:[{model_id:4,version_id:5,name:'Orders',version_number:1}]}
  }else if(url.endsWith('/jobs'))value={...scope,jobs:[]}
  else if(url.endsWith('/processing'))value={...scope,deliveries:[{source_file_name:'orders.csv',context:{...scope,upload_request_id:3}}],summary:{}}
  else if(url.includes('/gold-assessment'))value={...scope,status:'UNAVAILABLE',reason:'TRUSTED_GOLD_READER_UNAVAILABLE',publication_authorized:false}
  else if(url.endsWith('/csv-preview')){
   const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['csv_base64','mode'])
   assert.equal(atob(body.csv_base64),'id,amount\n1,1.20\n')
   assert.equal(options.credentials,'include');assert.equal(options.headers['X-CSRF-Token'],'synthetic-csrf')
   value={...scope,status:'PREVIEW',reason:null,total_rows:1,valid_rows:1,rejected_rows:0,rule_version:1,columns:['id','amount'],issues:[],publication_authorized:false}
   if(reply==='unavailable')value={...scope,status:'UNAVAILABLE',reason:'EXACT_APPROVED_POLICY_REQUIRED',publication_authorized:false}
   if(reply==='defer')await new Promise(resolve=>{defer=resolve})
  }else assert.fail('Unexpected product request '+url)
  return {ok:true,status:200,json:async()=>value}
 }
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 const action=async fn=>act(async()=>{fn();await new Promise(resolve=>setTimeout(resolve,10))})
 const render=(w='1',d='2')=>root.render(React.createElement(MemoryRouter,null,React.createElement(ProductReadiness,{workspaceId:w,datasetId:d})))
 const select=(element,value)=>{Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype,'value').set.call(element,value);element.dispatchEvent(new window.Event('change',{bubbles:true}))}
 const click=text=>{const button=[...host.querySelectorAll('button')].find(x=>x.textContent===text);assert.ok(button);assert.equal(button.disabled,false);button.dispatchEvent(new window.MouseEvent('click',{bubbles:true}))}
 const choose=()=>{select(host.querySelectorAll('select')[0],'3');const input=host.querySelector('input[type=file]');const bytes=new TextEncoder().encode('id,amount\n1,1.20\n');Object.defineProperty(input,'files',{configurable:true,value:[{name:'orders.csv',size:bytes.length,arrayBuffer:async()=>bytes.buffer}]});input.dispatchEvent(new window.Event('change',{bubbles:true}))}
 await action(()=>render())
 assert.match(host.textContent,/XLSX is not yet supported/)
 assert.match(host.textContent,/Processing submission disabled/)
 assert.match(host.textContent,/No durable jobs recorded/)
 assert.equal([...host.querySelectorAll('button')].find(x=>x.textContent==='Run preview').disabled,true)
 await action(choose);await action(()=>click('Run preview'))
 assert.match(host.textContent,/Preview only/);assert.match(host.textContent,/1 valid/)
 assert.ok(!host.textContent.includes('SYNTHETIC_PRIVATE'))
 reply='unavailable'
 await action(()=>select(host.querySelectorAll('select')[1],'exact'))
 await action(()=>click('Run preview'))
 assert.match(host.textContent,/no approved exact monetary policy/)
 await action(()=>select(host.querySelectorAll('select')[2],'5'))
 await action(()=>click('Assess Gold compatibility'))
 assert.match(host.textContent,/Trusted Gold reading is not enabled/)
 reply='defer';await action(()=>click('Run preview'))
 await action(()=>render('9','10'))
 assert.ok(!host.textContent.includes('no approved exact monetary policy'))
 await action(()=>defer())
 assert.ok(!host.textContent.includes('Preview only'),'old dataset request must not render after scope change')
 failCapabilities=true
 await action(()=>render('11','12'))
 assert.match(host.textContent,/Sign in again or check your workspace access/)
 assert.ok(!host.textContent.includes('SYNTHETIC_PRIVATE'))
 assert.ok(calls.filter(c=>c.options.method==='POST').every(c=>c.url.endsWith('/csv-preview')))
 const {DatasetProcessingSummary}=require('../src/features/ingestion/components/DatasetProcessingSummary.tsx')
 await action(()=>root.render(React.createElement(DatasetProcessingSummary,{data:{deliveries:[],summary:{total:1,pending:1,successful:0,failed:0,awaiting_rules:0,needs_attention:0,processing:0}},busy:false,executionDisabled:true,process:()=>assert.fail('Disabled execution fired')})))
 const disabledRun=[...host.querySelectorAll('button')].find(x=>x.textContent.includes('Process Pending Deliveries'))
 assert.ok(disabledRun.disabled)
 await action(()=>disabledRun.dispatchEvent(new window.MouseEvent('click',{bubbles:true})))
 await act(async()=>root.unmount());await window.happyDOM.close()
 console.log('Product readiness workflow/security checks passed (synthetic DOM/API, not live browser/cloud).')
}
main().catch(e=>{console.error(e);process.exitCode=1})
