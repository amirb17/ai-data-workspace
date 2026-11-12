// Real React DOM and session client; HTTP responses below are controlled fakes.
const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript')
async function main(){
 const {Window}=await import('happy-dom'),window=new Window()
 for(const name of ['window','document','HTMLElement','HTMLTextAreaElement','HTMLInputElement','HTMLSelectElement','Element','Node','Event','MouseEvent','navigator'])Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.IS_REACT_ACT_ENVIRONMENT=true
 for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText,file)
 const React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),{MemoryRouter}=require('react-router-dom')
 const {WarehouseSqlWorkspace}=require('../src/features/workspaces/warehouseSql/WarehouseSqlPage.tsx')
 const {LaunchContext}=require('../src/services/publicLaunchContext.ts')
 require('../src/services/api/client.ts').setIdentityMode('session')
 const handle='a'.repeat(32)
 const host=document.createElement('div');document.body.append(host);let root=createRoot(host),calls=[],state='SUCCEEDED',failure='',pending=null,resolvePending,rows=[['approved-value']],lostAdmission=false,lastKey,admissions=[]
 const model={model_id:10,model_version_id:11,version_number:1,name:'Orders',model_type:'FACT',status:'APPROVED',columns:[{name:'id',type:'STRING',role:'IDENTITY'}],sources:[],resource_registered:true,queryable:false,serving_status:'NOT_VERIFIED'}
 const status=()=>({attempt_handle:handle,status:state,results_available:state==='SUCCEEDED',query_execution_enabled:false})
 global.fetch=async(url,options={})=>{
  calls.push({url,options})
  if(url.endsWith('/auth/csrf'))return {ok:true,status:200,json:async()=>({csrf_token:'synthetic-csrf'})}
  const workspace=Number(url.match(/workspaces\/(\d+)/)[1])
  let value,code=200
  if(url.endsWith('/catalog'))value={workspace_id:workspace,models:[model,{...model,model_id:20,name:'Customers',model_type:'DIMENSION'}],query_execution_enabled:false}
  else if(failure){code=Number(failure);value={detail:'internal-project.secret-table credential-do-not-echo'}}
  else if(url.endsWith('/validate'))value={valid:true,model_id:10,model_version_id:11,columns:['id'],row_limit:100,estimated_bytes:1024,budget_decision:'ALLOWED',reason:'WITHIN_BUDGET',query_execution_enabled:false}
  else if(url.endsWith('/queries')){
   const body=JSON.parse(options.body);admissions.push(body);lastKey=body.idempotency_key
   if(lostAdmission){lostAdmission=false;throw Error('lost reply')}
   value={attempt_handle:handle,reused:admissions.length>1,query_execution_enabled:false}
  }else if(url.endsWith('/results'))value={attempt_handle:handle,columns:['id'],rows,has_more:false,bytes_encoded:32,query_execution_enabled:false}
  else value=status()
  if(pending&&url.endsWith(pending))await new Promise(resolve=>{resolvePending=resolve})
  return {ok:code===200,status:code,json:async()=>value}
 }
 const tick=()=>new Promise(r=>setTimeout(r,0))
 const action=async fn=>act(async()=>{fn();await tick()})
 const button=name=>[...host.querySelectorAll('button')].find(b=>b.textContent===name)
 const click=b=>{assert.ok(b,'button exists');b.dispatchEvent(new window.MouseEvent('click',{bubbles:true}))}
 async function mount(workspace='1',restricted=false){await action(()=>root.render(React.createElement(MemoryRouter,null,React.createElement(LaunchContext.Provider,{value:restricted},React.createElement(WarehouseSqlWorkspace,{workspace})))))}
 async function fresh(){await act(async()=>root.unmount());root=createRoot(host);calls=[];state='SUCCEEDED';failure='';pending=null;rows=[['approved-value']];admissions=[];await mount()}
 async function draft(){const select=host.querySelector('select');await action(()=>{select.value='10';select.dispatchEvent(new window.Event('change',{bubbles:true}))});await action(()=>click(button('Use selected model columns')));assert.equal(host.querySelector('textarea').value,'SELECT "id" FROM model_10 LIMIT 100')}
 async function admitted(){await draft();await action(()=>click(button('Validate SQL')));await action(()=>click(button('Admit query')));assert.ok(host.textContent.includes(handle))}
 let checks=0
 await mount();assert.ok(host.textContent.includes('Customers (DIMENSION'));assert.ok(host.textContent.includes('dataset / Gold SQL'));assert.ok(!host.textContent.includes('Estimated bytes:'));checks++
 await admitted();assert.ok(host.textContent.includes('Estimated bytes: 1,024'));assert.equal(JSON.parse(calls.find(c=>c.url.endsWith('/validate')).options.body).model_id,10);assert.equal(lastKey.length,36);assert.ok(calls.filter(c=>c.options.method==='POST').every(c=>c.options.credentials==='include'&&c.options.headers['X-CSRF-Token']==='synthetic-csrf'));checks++
 pending='/execute';await action(()=>{click(button('Execute original query'));click(button('Execute original query'))});assert.equal(calls.filter(c=>c.url.endsWith('/execute')).length,1);assert.ok(button('Execute original query').disabled);await action(()=>{pending=null;resolvePending()});assert.ok(host.textContent.includes('Status: SUCCEEDED'));checks++
 await action(()=>click(button('Load verified results')));assert.equal(host.querySelector('tbody td').textContent,'approved-value');assert.ok(!host.textContent.includes('jobReference'));checks++
 rows=[];await action(()=>click(button('Load verified results')));assert.ok(host.textContent.includes('No result rows.'));checks++
 failure='403';await action(()=>click(button('Refresh original status')));assert.ok(host.textContent.includes('Sign in again'));assert.equal(host.querySelector('table'),null);assert.equal(button('Execute original query'),undefined);assert.equal(host.querySelector('select'),null);checks++
 await fresh();state='RUNNING';await admitted();await action(()=>click(button('Execute original query')));assert.ok(host.textContent.includes('Status: RUNNING'));state='UNCERTAIN';await action(()=>click(button('Refresh original status')));assert.ok(host.textContent.includes('Status: UNCERTAIN'));assert.ok(button('Execute original query').disabled);assert.ok(button('New query').disabled);state='CANCELLED';await action(()=>click(button('Request cancellation')));assert.ok(host.textContent.includes('Status: CANCELLED'));assert.equal(calls.filter(c=>c.url.endsWith('/cancel')).length,1);assert.ok(button('Load verified results').disabled);assert.equal(calls.filter(c=>c.url.endsWith('/execute')).length,1);checks++
 await fresh();await admitted();failure='503';await action(()=>click(button('Execute original query')));assert.ok(host.textContent.includes('runtime is unavailable or disabled'));assert.ok(!host.textContent.includes('credential-do-not-echo'));assert.ok(button('Execute original query').disabled);assert.ok(host.textContent.includes(handle));checks++
 await fresh();await draft();await action(()=>click(button('Validate SQL')));lostAdmission=true;await action(()=>click(button('Admit query')));const retained=lastKey;assert.ok(button('New query').disabled);await action(()=>click(button('Retry original admission')));assert.equal(lastKey,retained);assert.equal(admissions[0].sql,admissions[1].sql);checks++
 await fresh();await admitted();pending='/execute';await action(()=>click(button('Execute original query')));await mount('2');assert.ok(!host.textContent.includes(handle));assert.equal(host.querySelector('textarea').value,'');await action(()=>{pending=null;resolvePending()});assert.equal(host.querySelector('table'),null);assert.ok(!host.textContent.includes(handle));assert.ok(calls.some(c=>c.url.includes('/workspaces/2/warehouse/catalog')));checks++
 await fresh();await admitted();failure='401';await action(()=>click(button('Refresh original status')));assert.equal(host.querySelector('textarea'),null);assert.ok(host.textContent.includes('Session or access changed'));assert.equal(button('Execute original query'),undefined);checks++
 await fresh();await admitted();await action(()=>click(button('Execute original query')));await action(()=>click(button('Load verified results')));await action(()=>window.dispatchEvent(new window.StorageEvent('storage',{key:'datarise-auth-change',newValue:'changed'})));assert.equal(host.querySelector('table'),null);assert.equal(host.querySelector('select'),null);assert.equal(button('Load verified results'),undefined);checks++
 await fresh();failure='400';await draft();await action(()=>click(button('Validate SQL')));assert.ok(host.textContent.includes('Warehouse SQL was rejected'));assert.ok(button('Admit query').disabled);assert.ok(!host.textContent.includes('credential-do-not-echo'));checks++
 await fresh();await mount('1',true);assert.ok(button('Validate SQL').disabled);assert.ok(button('Admit query').disabled);assert.ok(host.textContent.includes('Public read-only mode blocks'));assert.equal(calls.filter(c=>c.options.method==='POST').length,0);checks++
 await fresh();await admitted();state='EXPIRED';await action(()=>click(button('Refresh original status')));assert.ok(host.textContent.includes('Status: EXPIRED'));assert.ok(button('Execute original query').disabled);checks++
 await fresh();state='ADMITTED';const input=host.querySelector('#warehouse-resume');await action(()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,handle);input.dispatchEvent(new window.Event('input',{bubbles:true}))});await action(()=>host.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));assert.ok(host.textContent.includes('Status: ADMITTED'));assert.ok(!button('Execute original query').disabled);assert.equal(admissions.length,0);state='SUCCEEDED';await action(()=>click(button('Execute original query')));assert.ok(host.textContent.includes('Status: SUCCEEDED'));checks++
 await fresh();state='FAILED';await admitted();await action(()=>click(button('Execute original query')));assert.ok(host.textContent.includes('Status: FAILED'));assert.ok(button('Load verified results').disabled);assert.ok(button('Request cancellation').disabled);checks++
 await fresh();await draft();failure='503';await action(()=>click(button('Validate SQL')));assert.ok(host.textContent.includes('Live BigQuery is not enabled'));assert.ok(button('Admit query').disabled);assert.equal(admissions.length,0);checks++
 await fresh();await draft();await action(()=>click(button('Run query')));assert.ok(host.textContent.includes('Status: SUCCEEDED'));assert.equal(host.querySelector('tbody td').textContent,'approved-value');assert.equal(calls.filter(c=>c.url.endsWith('/validate')).length,1);assert.equal(calls.filter(c=>c.url.endsWith('/execute')).length,1);assert.equal(admissions.length,1);checks++
 await act(async()=>root.unmount());await window.happyDOM.close();console.log(`${checks} Warehouse SQL interaction scenarios passed (controlled HTTP, real session/CSRF client and React DOM)`)
}
main().catch(error=>{console.error(error);process.exitCode=1})
