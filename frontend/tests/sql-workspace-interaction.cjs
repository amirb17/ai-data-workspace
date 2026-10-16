// Component integration: controlled API responses exercise UI state, not execution semantics.
const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript'),path=require('node:path')
async function main(){
 const {Window}=await import('happy-dom'),window=new Window({width:390,height:844})
 for(const name of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','Element','Node','Event','MouseEvent','KeyboardEvent','PointerEvent','CSS','navigator','sessionStorage'])Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.requestAnimationFrame=window.requestAnimationFrame.bind(window);global.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);global.IS_REACT_ACT_ENVIRONMENT=true;window.HTMLElement.prototype.scrollIntoView=function(){}
 for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
 const React=require('react'),{act}=React,{createRoot}=require('react-dom/client')
 // CodeMirror is verified in the actual browser; this stand-in isolates authoring state tests.
 require.cache[require.resolve('../src/features/workspaces/sql/SqlEditor.tsx')]={exports:{SqlEditor:({value,onChange})=>React.createElement('textarea',{'aria-label':'SQL editor',value,onChange:e=>onChange(e.target.value)})}}
 const {SqlWorkspace}=require('../src/features/workspaces/sql/SqlPage.tsx');const {MemoryRouter}=require('react-router-dom')
 const column=(name,type,role='UNKNOWN',sensitive=false)=>({name,type,physical_type:type,role,key:false,time:type==='DATE',sensitive,queryable:!sensitive,description:''})
 const catalog={workspace_id:1,workspace_name:'Synthetic UI test',datasets:[{dataset_id:1,name:'Safe source',alias:'safe_d1',queryable:true,freshness:'Current',reason:'',row_count:2,columns:[column('status','TEXT','STATUS'),column('amount','DECIMAL','MEASURE'),column('secret','TEXT','UNKNOWN',true)],updated_at:null},{dataset_id:2,name:'Old source',alias:'old_d2',queryable:false,freshness:'Refresh required',reason:'Refresh analytics first',row_count:null,columns:[],updated_at:null}],relationships:[],capabilities:{functions:['COUNT','SUM'],limits:{output_rows:500,seconds:30,text_length:20000,history:100},join_policy:'Confirmed lookups only.',sensitivity_policy:'Sensitive fields unavailable.'}}
 const result={workspace_id:1,execution_id:1,sql_text:'SELECT COUNT(*) FROM safe_d1',status:'SUCCEEDED',diagnostics:[],columns:[{name:'count_rows',type:'BIGINT'}],rows:[[2]],row_count:1,duration_ms:10,freshness:'Current',sources:[{name:'Safe source',dataset_id:1,state_id:888,gold_run_id:999}],relationships:[],executed_at:'2026-10-12T00:00:00Z',engine:'DuckDB'}
 let calls=[],saved=[{query_id:1,workspace_id:1,name:'Saved count',description:'',sql_text:result.sql_text,version:1,updated_at:result.executed_at}],clipboard='',slow=false,late
 Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{clipboard=text}},configurable:true})
 global.fetch=async(url,options={})=>{const body=options.body?JSON.parse(options.body):{};calls.push({url,body,method:options.method,signal:options.signal});const ws=Number(url.match(/workspaces\/(\d+)/)[1]);let value
  if(url.endsWith('/catalog'))value={...catalog,workspace_id:ws,workspace_name:ws===1?catalog.workspace_name:'Second workspace',datasets:ws===1?catalog.datasets:[]}
  else if(url.endsWith('/builder')){if(slow){slow=false;return new Promise(resolve=>late=()=>resolve({ok:true,json:async()=>({workspace_id:ws,sql_text:'STALE PREVIEW',diagnostics:[]})}))}value={workspace_id:ws,status:'VALID',sql_text:`SELECT ${body.selections.map(s=>s.aggregation?s.aggregation+'(*)':s.column).join(', ')} FROM "${body.dataset}" LIMIT ${body.limit}`,diagnostics:[]}}
  else if(url.endsWith('/validate'))value={workspace_id:ws,status:'VALID',formatted_sql:body.sql_text+'\n',diagnostics:[]}
  else if(url.endsWith('/execute'))value={...result,workspace_id:ws}
  else if(url.includes('/executions'))value=ws===1?[{...result,rows:[],historical:true}]:[]
  else if(url.includes('/queries')&&options.method==='POST'){value={...saved[0],query_id:2,name:body.name,sql_text:body.sql_text};saved.push(value)}
  else if(url.includes('/queries')&&options.method==='PATCH'){value={...saved[0],...body,version:2};saved[0]=value}
  else if(url.includes('/queries')&&options.method==='DELETE'){saved=[];value={deleted:true}}
  else value=ws===1?saved:[]
  return {ok:true,json:async()=>value}
 }
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 const flush=()=>new Promise(r=>setTimeout(r,0)),action=async fn=>act(async()=>{fn();await flush()}),wait=async()=>act(async()=>{await new Promise(r=>setTimeout(r,380))})
 const click=el=>{assert.ok(el,'control exists');el.dispatchEvent(new window.MouseEvent('click',{bubbles:true}))}
 const button=text=>[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===text)
 const combo=label=>[...document.querySelectorAll('button[role="combobox"]')].find(el=>document.getElementById(el.getAttribute('aria-labelledby'))?.textContent===label)
 const choose=async(label,text)=>{await action(()=>click(combo(label)));await action(()=>click([...document.querySelectorAll('[role="option"]')].find(el=>el.textContent.startsWith(text))))}
 const fill=async(el,value)=>action(()=>{Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new window.Event('input',{bubbles:true}))})
 await act(async()=>{root.render(React.createElement(MemoryRouter,null,React.createElement(SqlWorkspace,{workspace:'1',key:'1'})));await flush()})
 assert.equal(button('Visual Builder').getAttribute('aria-pressed'),'true');assert.ok(button('SQL Editor'));assert.equal(document.querySelectorAll('nav[aria-label="Builder clauses"] button').length,7)
 await action(()=>click(button('Safe sourceCurrent2 columns · 2 rows')||[...host.querySelectorAll('button')].find(el=>el.textContent.startsWith('Safe source'))))
 await action(()=>click(button('Add Column')));await wait()
 assert.ok(host.querySelector('pre').textContent.includes('status'));assert.equal(calls.filter(c=>c.url.endsWith('/execute')).length,0,'preview never auto-executes')
 await action(()=>click(button('Copy SQL')));assert.equal(clipboard,host.querySelector('pre').textContent)
 await action(()=>click(combo('Selection 1 column')));assert.ok(![...document.querySelectorAll('[role="option"]')].some(el=>el.textContent.startsWith('secret')));assert.ok(document.body.textContent.includes('Dimensions'));await action(()=>document.activeElement.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})))
 await action(()=>click(button('Run Query')));assert.ok(host.textContent.includes('Query results'));assert.ok(host.textContent.includes('Search in results'))
 await action(()=>click(button('Query details')));assert.ok(host.textContent.includes('Safe source'));assert.ok(!host.textContent.includes('888')&&!host.textContent.includes('999'))
 await action(()=>click(button('Results')));await fill(host.querySelector('input[placeholder="Search returned rows…"]'),'does not match');assert.equal(host.querySelectorAll('tbody tr').length,0)
 await action(()=>click(button('Open in SQL Editor')));assert.equal(button('SQL Editor').getAttribute('aria-pressed'),'true');const editor=host.querySelector('[aria-label="SQL editor"]');assert.ok(editor.value.includes('SELECT'))
 await action(()=>click(button('Validate')));assert.ok(host.textContent.includes('SQL validated'))
 await action(()=>click(button('Format')));assert.ok(editor.value.endsWith('\n'))
 await fill(editor,'SELECT COUNT(*) FROM safe_d1 WHERE amount > 0')
 await action(()=>click(button('Visual Builder')));assert.ok(document.querySelector('[role="dialog"]').textContent.includes('cannot be edited safely'));await action(()=>click(button('Keep SQL Editor')));assert.equal(document.querySelector('[role="dialog"]'),null)
 await action(()=>click(button('New Query')));assert.ok(document.querySelector('[role="dialog"]').textContent.includes('unsaved changes'));await action(()=>click(button('Keep editing')));assert.ok(editor.value.includes('WHERE'))
 await action(()=>click(button('New Query')));await action(()=>click(button('Discard and start new query')));assert.equal(button('Visual Builder').getAttribute('aria-pressed'),'true');assert.equal(host.querySelectorAll('fieldset').length,0)
 await action(()=>click(button('Saved Queries')));assert.ok(document.querySelector('[role="dialog"]').textContent.includes('Saved count'));await fill(document.querySelector('[role="dialog"] input'),'absent');assert.ok(document.body.textContent.includes('No matching queries'));await fill(document.querySelector('[role="dialog"] input'),'Saved');await action(()=>click(button('Open')));assert.equal(button('SQL Editor').getAttribute('aria-pressed'),'true')
 await fill(host.querySelector('[aria-label="SQL editor"]'),'SELECT SUM(amount) FROM safe_d1');await action(()=>click(button('Saved Queries')));await action(()=>click(button('Rename')));await fill(document.querySelector('[role="dialog"] input'),'Renamed count');await action(()=>click([...document.querySelectorAll('[role="dialog"] button')].find(el=>el.textContent==='Save query')));assert.ok(calls.some(c=>c.method==='PATCH'&&c.body.expected_version===1&&c.body.sql_text===result.sql_text));assert.equal(host.querySelector('[aria-label="SQL editor"]').value,'SELECT SUM(amount) FROM safe_d1')
 await action(()=>click(button('Query History')));assert.ok(document.querySelector('[role="dialog"]').textContent.includes('1 rows'));await action(()=>click(button('Open SQL')));await action(()=>click(button('Discard and continue')));assert.ok(host.textContent.includes('Rows are not retained'))
 await action(()=>click(button('Run Query')));assert.ok(host.textContent.includes('Query results'))
 await action(()=>click(button('Saved Queries')));await action(()=>click(button('Duplicate')));await action(()=>click(button('Discard and continue')))
 assert.ok(host.textContent.includes('Duplicate draft opened'))
 assert.equal(host.querySelector('[aria-labelledby="sql-results-title"]'),null,'a duplicated draft cannot retain another execution result')
 assert.ok(!host.textContent.includes('Rows are not retained'))
 await action(()=>click(button('Saved Queries')));await action(()=>click(button('Delete…')));assert.ok(document.querySelector('[role="dialog"]').textContent.includes('Delete saved query?'));await action(()=>click(button('Cancel')));assert.ok(!calls.some(c=>c.method==='DELETE'),'cancel does not delete')
 await action(()=>click(button('Data Catalog')));assert.ok(document.querySelector('[role="dialog"]').getAttribute('aria-label')==='Data Catalog');await action(()=>click(document.querySelector('[aria-label="Close Data Catalog"]')))
 // An out-of-order Builder response cannot enable stale SQL after changing a selection.
 await action(()=>click(button('New Query')));if(button('Discard and start new query'))await action(()=>click(button('Discard and start new query')))
 await action(()=>click([...host.querySelectorAll('button')].find(el=>el.textContent.startsWith('Safe source'))));slow=true;await action(()=>click(button('Add Column')));await wait();assert.ok(late)
 await choose('Selection 1 column','amount');await wait();assert.ok(host.querySelector('pre').textContent.includes('amount'));await action(()=>late());assert.ok(!host.textContent.includes('STALE PREVIEW'))
 await act(async()=>{root.render(React.createElement(MemoryRouter,null,React.createElement(SqlWorkspace,{workspace:'2',key:'2'})));await flush()});assert.ok(calls.some(call=>call.url.includes('/workspaces/2/sql/catalog')));assert.ok(host.textContent.includes('SQL isn’t ready yet'));assert.equal(host.querySelector('pre'),null);assert.equal(document.querySelector('[role="dialog"]'),null);assert.ok(!host.textContent.includes('Renamed count'));assert.equal(button('Run Query').disabled,true);assert.ok(host.textContent.includes('SQL isn’t ready yet'));assert.equal(host.querySelectorAll('nav[aria-label="Builder clauses"] button').length,0);assert.equal(button('Visual Builder').disabled,true)

 const {SqlResults}=require('../src/features/workspaces/sql/SqlResults.tsx')
 const chartResult={...result,columns:[{name:'category',type:'VARCHAR'},{name:'day',type:'DATE'},{name:'count',type:'BIGINT'},{name:'exact',type:'DECIMAL'}],rows:[['a','2026-01-01',1,'1.000000000000000001'],['b','2026-01-02',2,'2.000000000000000001']],row_count:2}
 await act(async()=>root.render(React.createElement(SqlResults,{result:chartResult})))
 await action(()=>click(button('Chart')))
 await action(()=>click(combo('Y axis')));assert.ok(![...document.querySelectorAll('[role="option"]')].some(el=>el.textContent.includes('exact')));await action(()=>document.activeElement.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})))
 await choose('X axis','category');await choose('Y axis','count')
 await choose('Chart type','Line');assert.ok(combo('X axis').textContent.includes('Choose'));assert.ok(combo('Y axis').textContent.includes('Choose'))
 await action(()=>click(combo('X axis')));assert.deepEqual([...document.querySelectorAll('[role="option"]')].map(el=>el.textContent),['dayDATE']);await action(()=>document.activeElement.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})))
 await act(async()=>root.unmount());await window.happyDOM.close()
 console.log('SQL workspace integration passed: default modes, previews/no execution, copy, typed fields, run/results/search/details, validation/format, transitions, guards, drawers/search/rename/history/delete cancel, async race, workspace reset')
}
main().catch(error=>{console.error(error);process.exit(1)})

