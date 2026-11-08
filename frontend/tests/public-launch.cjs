// Real React policy states and controls. Synthetic local API only.
const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript')
async function main(){
 const {Window}=await import('happy-dom'),window=new Window()
 for(const name of ['window','document','HTMLElement','HTMLInputElement','Element','Node','Event','MouseEvent','navigator'])Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.IS_REACT_ACT_ENVIRONMENT=true
 for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
 const React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),{MemoryRouter}=require('react-router-dom')
 const {PublicLaunchPolicy}=require('../src/components/layout/PublicLaunchPolicy.tsx')
 const {usePublicReadOnly}=require('../src/services/publicLaunchContext.ts')
 const {DeliveryMoreMenu}=require('../src/features/files/components/DeliveryMoreMenu.tsx')
 let mode='csv-readonly',failure=false,archived=0,calls=0
 global.fetch=async(url)=>{assert.ok(url.endsWith('/auth/config'));calls++;if(failure)throw new Error('offline');return {ok:true,status:200,json:async()=>({identity_mode:'session',google_available:false,public_launch_mode:mode})}}
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 function Child(){const readOnly=usePublicReadOnly();return React.createElement('div',null,React.createElement('p',{'data-testid':'policy'},readOnly?'restricted':'local'),React.createElement(DeliveryMoreMenu,{readOnly,context:{status:'SUCCESS',archived_at:null,stages:{bronze:'SUCCESS',silver:'SUCCESS',gold:'SUCCESS'},output_rows:2},fileName:'orders.csv',processingHref:'/processing',onViewDetails(){},onArchive(){archived++}}))}
 async function render(key){await act(async()=>{root.render(React.createElement(MemoryRouter,null,React.createElement(PublicLaunchPolicy,{key},React.createElement(Child))));await new Promise(r=>setTimeout(r,0))})}
 await render('public')
 assert.match(host.textContent,/Public read-only release/);assert.match(host.textContent,/XLSX is not supported/)
 assert.equal(host.querySelector('[data-testid=policy]').textContent,'restricted')
 const archive=[...host.querySelectorAll('button')].find(b=>/Archive|Remove/i.test(b.textContent));assert.ok(archive);assert.equal(archive.disabled,true)
 await act(async()=>archive.click());assert.equal(archived,0)
 mode='off';await render('development');assert.equal(host.querySelector('[data-testid=policy]').textContent,'local')
 assert.doesNotMatch(host.textContent,/Public read-only release/)
 failure=true;await render('offline');assert.match(host.textContent,/Website permissions are unavailable/)
 assert.equal(host.querySelector('[data-testid=policy]').textContent,'restricted')
 assert.equal(calls,3)
 await act(async()=>root.unmount());await window.happyDOM.close()
 console.log('Public launch states and disabled delivery changes passed')
}
main().catch(error=>{console.error(error);process.exitCode=1})
