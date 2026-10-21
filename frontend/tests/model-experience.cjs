const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript'),path=require('node:path')
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
require.extensions['.css']=()=>{}
const React=require('react'),{currentMeaning,description,modelRelationships,relationshipsNeedRefresh,layoutDatasets}=require('../src/features/workspaces/model/modelView.ts')
const make=(id,name)=>({dataset:{id,name,description:'',status:'ACTIVE',updatedAt:'Unavailable'},profile:{freshness:'READY',profile_ready:true,profile_id:id,current_state_id:id,state_version:1,profile_version:1,profile_timestamp:'2026-10-18T00:00:00Z',summary:{row_count:7,column_count:2,business_key:['id'],event_time_column:null},columns:[{original_name:'id',canonical_type:'TEXT',authoritative_business_key:true,null_ratio:0,distinct_count:7,sensitivity_hints:[]},{original_name:'amount',canonical_type:'DECIMAL',authoritative_business_key:false,null_ratio:0,distinct_count:7,sensitivity_hints:[]}]},meaning:{status:'READY',source_profile_id:id,source_state_id:id,suggestion:{reasoning:{entity:{primary:{label:name,rationale:`Current interpretation for ${name}`}},columns:[{column_name:'amount',business_meaning:'Recorded amount',suggested_role:'MEASURE',warnings:[]}]}}}})
const a=make(1,'Customers'),b=make(2,'Orders')
const evidence={workspace_id:1,candidate_key:'pair',parent:{dataset_id:1,dataset_name:'Customers',columns:['id']},child:{dataset_id:2,dataset_name:'Orders',columns:['customer_id']},source_pins:[{dataset_id:1,state_version:1,profile_version:1},{dataset_id:2,state_version:1,profile_version:1}],candidate_cardinality:'ONE_TO_MANY',signals:{overlap:{child_to_parent_coverage:1}}}
const reviewed={candidate_id:1,status:'CONFIRMED',structural_status:'CURRENT',verification_status:'CURRENT',evidence,reviewed_at:'2026-10-18T00:00:00Z'}
const view={workspace_id:1,status:'READY',reviewed_relationships:[reviewed],candidates:[]}
assert.equal(modelRelationships(view,[a,b]).length,1)
for(const change of [{status:'REJECTED'},{structural_status:'REVALIDATION_REQUIRED'},{verification_status:'STALE'}])assert.equal(modelRelationships({...view,reviewed_relationships:[{...reviewed,...change}]},[a,b]).length,0)
assert.equal(modelRelationships(view,[a]).length,0)
assert.equal(modelRelationships(view,[a,{...b,profile:{...b.profile,state_version:99}}]).length,0,'Mixed snapshots cannot display a current edge')
assert.equal(relationshipsNeedRefresh(view,modelRelationships(view,[a,b])),false)
assert.equal(relationshipsNeedRefresh(view,modelRelationships(view,[a,{...b,profile:{...b.profile,state_version:99}}])),true,'Mixed snapshots must also withhold the all-current status')
assert.equal(modelRelationships({...view,workspace_id:99},[a,b]).length,0)
const suggestion={candidate_id:2,review_status:'REVIEW_REQUIRED',evidence:{...evidence,candidate_key:'other'}}
assert.equal(modelRelationships({...view,candidates:[suggestion]},[a,b])[1].suggested,true)
assert.equal(modelRelationships({...view,status:'STALE',candidates:[suggestion]},[a,b]).length,1)
assert.equal(description(a),'Current interpretation for Customers')
for(const value of [{...a,meaning:{...a.meaning,status:'STALE'}},{...a,meaning:{...a.meaning,source_profile_id:999}},{...a,profile:{...a.profile,freshness:'STALE'}}]){assert.equal(currentMeaning(value),false);assert.equal(description(value),'Description not available yet')}
const relations=modelRelationships(view,[a,b])
assert.deepEqual(layoutDatasets([1,2],relations),layoutDatasets([2,1],relations))
assert.ok(layoutDatasets([1,2],relations)[1].position.x>layoutDatasets([1,2],relations)[0].position.x)
assert.equal(layoutDatasets([1,2],[...relations,{...relations[0],evidence:{...evidence,parent:evidence.child,child:evidence.parent}}]).length,2)
async function main(){
 const {Window}=await import('happy-dom'),window=new Window({width:390,height:844})
 for(const name of ['window','document','HTMLElement','HTMLInputElement','Element','Node','Event','MouseEvent','KeyboardEvent','navigator'])Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.IS_REACT_ACT_ENVIRONMENT=true
 const {getWorkspaceModel}=require('../src/services/api/workspaceModel.ts')
 global.fetch=async()=>({ok:true,json:async()=>({workspace_id:2,datasets:[a]})})
 await assert.rejects(getWorkspaceModel('1'),/another workspace/)
 const {act}=React,{createRoot}=require('react-dom/client'),{MemoryRouter}=require('react-router-dom')
 // Graph library interactions are checked in the browser; isolate product state here.
 require.cache[require.resolve('../src/features/workspaces/model/ModelGraph.tsx')]={exports:{ModelGraph:props=>React.createElement('button',{onClick:()=>props.select(2)},'Graph Orders')}}
 const {ModelContent}=require('../src/features/workspaces/model/WorkspaceModelPage.tsx')
 global.fetch=async()=>({ok:true,json:async()=>({workspace_id:1,dataset_id:2,deliveries:[],summary:{}})})
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 const action=async f=>act(async()=>{f();await new Promise(r=>setTimeout(r,0))})
 const button=text=>[...host.querySelectorAll('button')].find(el=>el.textContent.trim()===text)
 const render=async(extra={},key='1')=>action(()=>root.render(React.createElement(MemoryRouter,null,React.createElement(ModelContent,{workspace:'1',datasets:[a,b],relationships:relations,stale:false,reload:()=>{},key,...extra}))))
 await render();assert.ok(host.textContent.includes('All model information current'));assert.ok(!host.textContent.includes('profile_id'))
 await action(()=>button('Graph Orders').click());assert.ok(host.querySelector('[role="dialog"]').textContent.includes('Orders'));assert.ok(host.querySelector('[role="dialog"]').textContent.includes('Last updated'));assert.ok(!host.textContent.includes('Invalid Date'))
 await action(()=>button('Columns').click());assert.ok(host.textContent.includes('Recorded amount'));assert.ok(host.textContent.includes('PK'));assert.ok(host.textContent.includes('Measure'))
 const input=host.querySelector('input:not([placeholder])');await action(()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'amount');input.dispatchEvent(new window.Event('input',{bubbles:true}))});assert.equal(host.querySelectorAll('tbody tr').length,1)
 await action(()=>button('Relationships').click());assert.ok(host.querySelector('[role="dialog"]').textContent.includes('One record in Customers can match many records in Orders'))
 await action(()=>button('Quality').click());assert.ok(host.textContent.includes('Process a delivery'));assert.ok(host.textContent.includes('View Data Quality'))
 await action(()=>document.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));assert.equal(host.querySelector('[role="dialog"]'),null)
 await action(()=>host.querySelector('details summary').click());await action(()=>button('Inspect Orders').click());assert.ok(host.querySelector('[role="dialog"]'))
 await render({workspace:'2',datasets:[],relationships:[]},'2');assert.equal(host.querySelector('[role="dialog"]'),null);assert.ok(!host.textContent.includes('Customers'));assert.equal(host.querySelector('input').value,'')
 await render({datasets:[{...a,meaning:{...a.meaning,status:'STALE'}}],relationships:[],stale:true},'3');assert.ok(host.textContent.includes('Some model information needs refresh'));assert.ok(!host.textContent.includes('Current interpretation'))
 await render({datasets:[a,{...b,meaning:{...b.meaning,status:'NOT_GENERATED'}}],relationships:[]},'4');assert.ok(host.textContent.includes('1 of 2 dataset descriptions prepared'));assert.ok(host.textContent.includes('No confirmed current relationships'))
 await render({relationships:modelRelationships({...view,reviewed_relationships:[],candidates:[suggestion]},[a,b])},'5');await action(()=>button('Suggested relationships (1)').click());assert.ok(host.textContent.includes('Dashed connections'));assert.ok(!host.textContent.includes('Confirmed · Current'))
 await action(()=>button('Graph Orders').click());assert.ok(host.querySelector('a[href="/app/workspaces/1/analytics"]'));assert.ok(host.textContent.includes('Review suggested relationships'));
 await action(()=>root.unmount());window.happyDOM.abort();console.log('Model trust/freshness, layout, partial states, selection, columns, keys, quality, relationships, keyboard closure and workspace isolation passed')
}
main().catch(e=>{console.error(e);process.exitCode=1})
