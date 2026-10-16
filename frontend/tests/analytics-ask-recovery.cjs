// Actual page composition: analytics read failures must not remount a separate Ask session.
const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript')
async function main(){
 const {Window}=await import('happy-dom'),window=new Window()
 for(const name of ['window','document','HTMLElement','HTMLTextAreaElement','Element','Node','Event','MouseEvent','navigator'])Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.IS_REACT_ACT_ENVIRONMENT=true
 for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
 const React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),{MemoryRouter,Routes,Route}=require('react-router-dom')
 const {WorkspaceAnalyticsPage}=require('../src/pages/Workspace/Analytics/WorkspaceAnalyticsPage.tsx')
 const timers=[],realInterval=global.setInterval,realClear=global.clearInterval
 global.setInterval=callback=>{timers.push(callback);return callback};global.clearInterval=()=>{}
 let failed=false,history=[]
 const analytics={workspace_id:1,status:'NOT_READY',metrics:[],relationships:[],metrics_fresh:0,metrics_available:0,metrics_stale:0,metrics_failed:0,metrics_blocked:0,can_refresh:false,last_refreshed:null}
 global.fetch=async(url,options={})=>{
  if(url.endsWith('/analytics'))return {ok:!failed,status:failed?503:200,json:async()=>analytics}
  if(url.endsWith('/readiness'))return {ok:true,json:async()=>({workspace_id:1,ready:true,message:'Ready',suggested_questions:[]})}
  if(url.endsWith('/answers'))return {ok:true,json:async()=>({workspace_id:1,answers:history})}
  const body=JSON.parse(options.body)
  const answer={workspace_id:1,answer_id:1,question:body.question,status:'ANSWERED',freshness:'FRESH',computed_at:'2026-10-15T00:00:00Z',primary_answer:'1,000',result_kind:'scalar',value:1000,groups:[],comparison:null,dimensions:[],provenance:[],relationship_versions:[],interpretation:'',explanation:'',caveats:[],follow_ups:[],choices:[],reused:false}
  history=[answer];return {ok:true,json:async()=>answer}
 }
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 const action=async callback=>act(async()=>{callback();await new Promise(resolve=>setTimeout(resolve,0))})
 await action(()=>root.render(React.createElement(MemoryRouter,{initialEntries:['/workspaces/1/analytics']},React.createElement(Routes,null,React.createElement(Route,{path:'/workspaces/:workspaceId/analytics',element:React.createElement(WorkspaceAnalyticsPage)})))))
 const input=host.querySelector('textarea')
 await action(()=>{Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(input,'How many orders?');input.dispatchEvent(new window.Event('input',{bubbles:true}))})
 await action(()=>host.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})))
 assert.ok(host.querySelector('section[aria-labelledby="ask-workspace-title"] > article').textContent.includes('1,000'))
 failed=true;await action(()=>timers.forEach(callback=>callback()))
 assert.ok(host.querySelector('[role="alert"]').textContent.includes('Could not load workspace analytics'))
 assert.equal(host.querySelector('textarea'),input,'independent metric error preserves Ask instance')
 assert.ok(host.querySelector('section[aria-labelledby="ask-workspace-title"] > article').textContent.includes('1,000'))
 assert.ok(host.textContent.includes('Following up on the previous answer'))
 failed=false;await action(()=>timers.forEach(callback=>callback()))
 assert.equal(host.querySelector('[role="alert"]'),null)
 assert.equal(host.querySelector('textarea'),input)
 assert.ok(host.querySelector('section[aria-labelledby="ask-workspace-title"] > article').textContent.includes('1,000'))
 await act(async()=>root.unmount());global.setInterval=realInterval;global.clearInterval=realClear;await window.happyDOM.close()
 console.log('Analytics/Ask recovery passed: metric polling error hides metric state while preserving focused answer, input, context and recovery')
}
main().catch(error=>{console.error(error);process.exit(1)})
