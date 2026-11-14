// Actual release provider and Ask UI in a synthetic DOM; no browser/cloud claims.
const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript')
async function main(){
 const {Window}=await import('happy-dom'),window=new Window()
 for(const name of ['window','document','HTMLElement','HTMLTextAreaElement','Element','Node','Event','MouseEvent','navigator'])Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.IS_REACT_ACT_ENVIRONMENT=true
 for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
 const React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),{MemoryRouter}=require('react-router-dom')
 const {PublicLaunchPolicy}=require('../src/components/layout/PublicLaunchPolicy.tsx')
 const {WorkspaceAsk}=require('../src/features/workspaces/ask/WorkspaceAsk.tsx')
 const {WorkspaceCard}=require('../src/features/workspaces/components/WorkspaceCard.tsx')
 const api=require('../src/services/api/auth.ts')
 let config={identity_mode:'development',google_available:false,public_launch_mode:'csv-processing',processing_available:true,ask_ai_available:false}
 let calls=[]
 api.authConfig=async()=>config
 global.fetch=async(url)=>{calls.push(url);throw new Error('Ask must remain fail closed')}
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 const action=async fn=>act(async()=>{fn();await new Promise(r=>setTimeout(r,0))})
 const render=key=>root.render(React.createElement(MemoryRouter,null,React.createElement(PublicLaunchPolicy,{key},React.createElement(WorkspaceAsk,{workspaceId:'1'}),React.createElement(WorkspaceCard,{workspace:{id:1,name:'Commerce',status:'ACTIVE',description:'Sales operations',updatedAt:'Unavailable',datasetCount:1}}))))
 await action(()=>render('approved-processing'))
 assert.match(host.textContent,/CSV processing available/)
 assert.match(host.textContent,/Ask AI is awaiting provider/)
 assert.equal(host.querySelector('textarea').disabled,true)
 assert.equal(calls.length,0,'disabled Ask does not poll or invoke a provider')
 const link=host.querySelector('a[aria-label="Open workspace: Commerce"]')
 assert.ok(link)
 assert.equal(link.querySelector('button,a'),null,'the whole card has one accessible navigation target')
 assert.ok(!link.textContent.includes('Open Workspace'))
 assert.match(link.textContent,/1 dataset/)
 config={...config,public_launch_mode:'unknown',processing_available:true,ask_ai_available:true}
 await action(()=>render('unknown-mode'))
 assert.equal(host.querySelector('textarea').disabled,true)
 assert.equal(calls.length,0,'unknown mode cannot enable Ask via booleans')
 await act(async()=>root.unmount());await window.happyDOM.close()
 console.log('Release capability truth, disabled AI, unknown-mode denial and accessible workspace cards passed (synthetic DOM).')
}
main().catch(error=>{console.error(error);process.exit(1)})
