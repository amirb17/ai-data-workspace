// Static visual QA of real React components. Synthetic data, no API/cloud access.
const fs=require('node:fs'),path=require('node:path'),{createRequire}=require('node:module')
const req=createRequire(path.resolve('frontend/package.json'))
const ts=req('typescript'),React=req('react'),{renderToStaticMarkup}=req('react-dom/server'),{MemoryRouter}=req('react-router-dom')
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
const {IdentityContext}=require('../frontend/src/services/identityContext.ts')
const {ReleaseFeatures}=require('../frontend/src/services/releaseFeatures.ts')
const {AppShell}=require('../frontend/src/components/layout/AppShell.tsx')
const {WorkspaceCard}=require('../frontend/src/features/workspaces/components/WorkspaceCard.tsx')
const {DatasetProcessingSummary}=require('../frontend/src/features/ingestion/components/DatasetProcessingSummary.tsx')
const {DeliveryRuleBridge}=require('../frontend/src/features/ingestion/components/DeliveryRuleBridge.tsx')
const {WorkspaceAsk}=require('../frontend/src/features/workspaces/ask/WorkspaceAsk.tsx')
const h=React.createElement,out='docs/issue-1/evidence'
const assets='frontend/dist/assets',css=fs.readdirSync(assets).find(f=>/^index-.*\.css$/.test(f))
fs.copyFileSync(path.join(assets,css),path.join(out,'preview.css'))
const user={userId:1,displayName:'Demo Analyst',email:'demo@example.test',ownerKey:'synthetic',identityMode:'development'}
const features={checking:false,processing:true,rules:true,initialAppend:true,model:true,ask:false}
const workspace={id:1,name:'Retail operations',description:'Orders, customers and fulfilment.',datasetCount:3,status:'ACTIVE',updatedAt:'Unavailable'}
const context={upload_request_id:1,workspace_id:1,dataset_id:1,file_id:1,status:'READY_TO_PROCESS',rule_state:null,rule_version:null,can_continue:false,stages:{bronze:'PENDING',rules:'PENDING',silver:'PENDING',gold:'PENDING'},issue_summary:[],input_rows:3,column_count:3}
const batch={id:'upload-1',uploadRequestId:1,sourceFileId:1,sourceFileName:'orders.csv',createdAt:'2026-11-14T10:00:00Z',rowCount:3,columnCount:3}
function save(name,body){
 const tree=h(MemoryRouter,{initialEntries:['/app']},h(IdentityContext.Provider,{value:user},h(ReleaseFeatures.Provider,{value:features},h(AppShell,null,h('div',{className:'space-y-6'},h('aside',{className:'rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950'},'STATIC DESIGN PREVIEW / synthetic data / no live processing'),...body)))))
 fs.writeFileSync(path.join(out,'preview-'+name+'.html'),'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Issue 1 design preview</title><link rel="stylesheet" href="preview.css"></head><body><div id="root">'+renderToStaticMarkup(tree)+'</div></body></html>')
}
save('home',[h('header',{key:'heading'},h('h1',{className:'text-3xl font-semibold'},'Welcome, Demo'),h('p',{className:'mt-2 text-sm text-slate-600'},'Continue where you left off, or start with a new workspace.')),h('section',{key:'cards',className:'grid gap-4 md:grid-cols-2 xl:grid-cols-3'},h(WorkspaceCard,{workspace}),h(WorkspaceCard,{workspace:{...workspace,id:2,name:'Customer experience',description:'Service activity and customer feedback.',datasetCount:2}}))])
save('processing',[h('header',{key:'heading'},h('h1',{className:'text-2xl font-semibold'},'Orders / Processing'),h('p',{className:'mt-2 text-sm text-slate-600'},'Follow each delivery from source to trusted data.')),h(DatasetProcessingSummary,{key:'summary',data:{deliveries:[{context}],summary:{total:1,pending:1,processing:0,awaiting_rules:0,successful:0,failed:0,needs_attention:0}},busy:false,process(){}}),h(DeliveryRuleBridge,{key:'delivery',batch,context,workspaceId:'1',datasetId:'1',reload(){},datasetBusy:false})])
save('ask',[h('h1',{key:'heading',className:'text-2xl font-semibold'},'Workspace analytics'),h(WorkspaceAsk,{key:'ask',workspaceId:'1'})])
console.log('Saved three static component previews with explicit synthetic-data labels.')
