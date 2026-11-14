const assert=require('node:assert/strict')
const fs=require('node:fs'),ts=require('typescript')
async function main(){
 const {Window}=await import('happy-dom'),window=new Window()
 for(const name of ['window','document','HTMLElement','Element','Node','Event','MouseEvent','navigator'])
   Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.IS_REACT_ACT_ENVIRONMENT=true
 for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(
   ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),
   {compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
 const React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),{MemoryRouter,Routes,Route}=require('react-router-dom')
 const {WorkspaceAnalyticsPage}=require('../src/pages/Workspace/Analytics/WorkspaceAnalyticsPage.tsx')
 const calls=[]
 global.fetch=async (url)=>{
   const target=String(url);calls.push(target)
   let body
   if(target.endsWith('/auth/config'))body={identity_mode:'development',public_launch_mode:'csv-processing'}
   else if(target.endsWith('/workspaces/1/datasets'))body={workspace_id:1,datasets:[{workspace_id:1,dataset_id:1,dataset_name:'Orders',status:'ACTIVE'}]}
   else if(target.endsWith('/workspaces/1/datasets/1/analytics'))body={workspace_id:1,dataset_id:1,analytics_ready:true,freshness:'FRESH',trusted_rows:3,analytics_rows:3,analytics_built_at:null,kpis:[{kpi_name:'orders',label:'Total orders',value:3}]}
   else throw new Error('Unexpected restricted route: '+target)
   return {ok:true,status:200,json:async()=>body}
 }
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 await act(async()=>{root.render(React.createElement(MemoryRouter,{initialEntries:['/workspaces/1/analytics']},React.createElement(Routes,null,React.createElement(Route,{path:'/workspaces/:workspaceId/analytics',element:React.createElement(WorkspaceAnalyticsPage)}))));await new Promise(resolve=>setTimeout(resolve,0))})
 assert.ok(host.textContent.includes('Orders'))
 assert.ok(host.textContent.includes('Trusted records'))
 assert.ok(host.textContent.includes('Total orders'))
 assert.ok(!host.textContent.includes('Could not load workspace analytics'))
 assert.ok(!host.textContent.includes('Could not load workspace questions'))
 assert.ok(!calls.some(c=>c.endsWith('/workspaces/1/analytics')||c.includes('/questions')))
 assert.ok(calls.some(c=>c.endsWith('/datasets/1/analytics')))
 await act(async()=>root.unmount());await window.happyDOM.close()
 console.log('PUBLIC_WORKSPACE_ANALYTICS_LIVE_ROUTE_CONTRACT_PASSED')
}
main().catch(error=>{console.error(error);process.exitCode=1})
