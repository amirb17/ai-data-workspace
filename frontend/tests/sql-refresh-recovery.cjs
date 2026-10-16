// Real hook integration: slow metadata reads must not accumulate polling batches.
const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript')
async function main(){
 const {Window}=await import('happy-dom'),window=new Window()
 for(const name of ['window','document','HTMLElement','Element','Node','navigator'])Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.IS_REACT_ACT_ENVIRONMENT=true
 require.extensions['.ts']=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,file)
 const React=require('react'),{act}=React,{createRoot}=require('react-dom/client')
 const {useSqlWorkspace}=require('../src/features/workspaces/sql/useSqlWorkspace.ts')
 let tick,latest,calls=[],pending=[],slow=true,fail=false
 window.setInterval=callback=>{tick=callback;return 1};window.clearInterval=()=>{}
 const catalog={workspace_id:1,datasets:[],relationships:[],capabilities:{}}
 global.fetch=(url)=>{
  calls.push(url)
  const response=()=>({ok:!fail,status:fail?503:200,json:async()=>url.endsWith('/catalog')?catalog:[]})
  return slow?new Promise(resolve=>pending.push(()=>resolve(response()))):Promise.resolve(response())
 }
 function Probe(){latest=useSqlWorkspace('1');return React.createElement('p',null,latest.error|| (latest.catalog?'Current':'Loading'))}
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 const action=async callback=>act(async()=>{callback();await new Promise(resolve=>setTimeout(resolve,0))})
 await action(()=>root.render(React.createElement(Probe)))
 assert.equal(calls.length,3)
 await action(()=>{tick();tick();tick()})
 assert.equal(calls.length,3,'slow initial batch is not multiplied by polling')
 await action(()=>pending.splice(0).forEach(resolve=>resolve()))
 assert.equal(host.textContent,'Current')
 slow=false;fail=true;await action(()=>tick())
 assert.ok(host.textContent.includes('Could not refresh SQL sources'))
 assert.equal(latest.catalog,null,'failed source checks block execution metadata')
 fail=false;await action(()=>tick())
 assert.equal(host.textContent,'Current','successful recovery clears obsolete error')
 assert.equal(latest.error,'')
 let finish
 await action(()=>latest.action(()=>new Promise(resolve=>finish=resolve)))
 const before=calls.length;await action(()=>tick());assert.equal(calls.length,before,'polling stays paused during execution actions')
 await action(()=>finish())
 slow=true;await action(()=>tick());const during=calls.length
 await action(()=>{tick();tick()});assert.equal(calls.length,during,'later slow batches also remain bounded')
 await action(()=>pending.splice(0).forEach(resolve=>resolve()))
 await act(async()=>root.unmount());await window.happyDOM.close()
 console.log('SQL refresh recovery passed: bounded slow polling, failed-source blocking, error recovery and action exclusion')
}
main().catch(error=>{console.error(error);process.exit(1)})
