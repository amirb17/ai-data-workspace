// Real React interactions in a synthetic DOM; live browser evidence is recorded separately.
const assert = require('node:assert/strict'), fs = require('node:fs'), ts = require('typescript')
async function main() {
  const {Window} = await import('happy-dom'), window = new Window()
  for (const name of ['window','document','HTMLElement','HTMLTextAreaElement','Element','Node','Event','MouseEvent','KeyboardEvent','navigator']) Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
  global.IS_REACT_ACT_ENVIRONMENT = true
  for (const ext of ['.ts','.tsx']) require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
  const React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),{WorkspaceAsk}=require('../src/features/workspaces/ask/WorkspaceAsk.tsx')
  const base={workspace_id:1,question:'Old question',status:'FAILED',freshness:'UNAVAILABLE',computed_at:'2026-10-14T00:00:00Z',primary_answer:'Old failure',result_kind:null,value:null,groups:[],comparison:null,dimensions:[],provenance:[],relationship_versions:[],interpretation:'',explanation:'',caveats:[],follow_ups:[],choices:[],reused:false}
  let history=[{...base,answer_id:1},{...base,answer_id:2}], requests=[]
  global.fetch=async(url,options={})=>{
    const workspace=Number(url.match(/workspaces\/(\d+)/)[1]);let value
    if(url.endsWith('/readiness')) value={workspace_id:workspace,ready:true,message:'Current trusted data is ready.',suggested_questions:['How many orders?']}
    else if(url.endsWith('/answers')) value={workspace_id:workspace,answers:workspace===1?history:[]}
    else {
      const body=JSON.parse(options.body);requests.push(body)
      value={...base,workspace_id:workspace,answer_id:100+requests.length,question:body.question,status:'ANSWERED',freshness:'FRESH',primary_answer:'1,000',value:1000,result_kind:'scalar',follow_ups:['Break this down by status']}
      history=[value,...history]
    }
    return {ok:true,json:async()=>value}
  }
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
  const action=async fn=>act(async()=>{fn();await new Promise(resolve=>setTimeout(resolve,0))})
  const button=name=>[...host.querySelectorAll('button')].find(el=>el.textContent===name)
  const click=element=>element.dispatchEvent(new window.MouseEvent('click',{bubbles:true}))
  const fill=value=>{const input=host.querySelector('textarea');Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(input,value);input.dispatchEvent(new window.Event('input',{bubbles:true}))}
  const submit=()=>host.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}))
  await action(()=>root.render(React.createElement(WorkspaceAsk,{workspaceId:'1'})))
  assert.equal(host.querySelector('details').open,false,'history starts collapsed')
  assert.equal(host.querySelectorAll('section > article').length,0,'old failures do not dominate the question')
  await action(()=>fill('How many orders?'));await action(submit)
  assert.equal(host.querySelectorAll('section > article').length,1)
  assert.ok(host.querySelector('section > article').textContent.includes('1,000'))
  assert.equal(requests[0].previous_answer_id,undefined)
  await action(()=>click(button('Break this down by status')));await action(submit)
  assert.equal(requests[1].previous_answer_id,101,'follow-up retains the owned answered context')
  await action(()=>click(button('New analysis')))
  assert.equal(host.querySelectorAll('section > article').length,0)
  assert.equal(document.activeElement,host.querySelector('textarea'))
  await action(()=>fill('New topic'));await action(submit)
  assert.equal(requests[2].previous_answer_id,undefined,'new analysis clears prior context')
  await action(()=>root.render(React.createElement(WorkspaceAsk,{workspaceId:'2'})))
  assert.equal(host.querySelectorAll('article').length,0,'workspace change clears answers and history')
  assert.equal(host.querySelector('textarea').value,'')
  await act(async()=>root.unmount());await window.happyDOM.close()
  console.log('Product Ask interaction passed: collapsed history, current answer focus, follow-up, New analysis/focus, workspace reset')
}
main().catch(error=>{console.error(error);process.exitCode=1})
