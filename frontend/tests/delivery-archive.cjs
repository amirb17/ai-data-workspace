const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript'),React=require('react')
const {renderToStaticMarkup}=require('react-dom/server'),{MemoryRouter}=require('react-router-dom')
for(const extension of ['.ts','.tsx']) require.extensions[extension]=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8').replaceAll('import.meta.env','({ VITE_API_BASE_URL:"http://localhost:8000" })'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename)
const {archiveAction}=require('../src/features/files/deliveryArchive.ts')
const {ArchiveDeliveryDialog}=require('../src/features/files/components/ArchiveDeliveryDialog.tsx')
const {DeliveryMoreMenu}=require('../src/features/files/components/DeliveryMoreMenu.tsx')
const {archiveDelivery}=require('../src/services/api/deliveries.ts')
const base={upload_request_id:77,status:'READY_TO_PROCESS',stages:{silver:'PENDING',gold:'PENDING'}}
const render=(Component,context)=>renderToStaticMarkup(React.createElement(MemoryRouter,null,React.createElement(Component,{workspaceId:'10',datasetId:'20',datasetName:'Orders',fileName:'orders.csv',context,processingHref:'/app/workspaces/10/datasets/20/processing',onArchive:()=>{},onViewDetails:()=>{},onClose:()=>{},onArchived:()=>{}})))
for(const status of ['READY_TO_PROCESS','AWAITING_RULES','READY_FOR_SILVER']) assert.equal(archiveAction({...base,status}).label,'Remove Delivery')
assert.equal(archiveAction({...base,status:'SUCCESS'}).label,'Archive Delivery')
assert.equal(archiveAction({...base,status:'GOLD_FAILED',stages:{silver:'SUCCESS',gold:'FAILED'}}).label,'Archive Delivery')
for(const status of ['PROCESSING','BRONZE_PROCESSING','SILVER_PROCESSING','GOLD_PROCESSING']) {
  const html=render(DeliveryMoreMenu,{...base,status}); assert.match(html,/disabled/); assert.match(html,/Currently processing/)
}
const dialog=render(ArchiveDeliveryDialog,base)
assert.match(dialog,/role="dialog"/); assert.match(dialog,/aria-describedby/); assert.match(dialog,/orders.csv/); assert.match(dialog,/Orders dataset/); assert.match(dialog,/Cancel/); assert.match(dialog,/Remove Delivery/); assert.match(dialog,/max-h-\[90vh\]/)
const completed=render(ArchiveDeliveryDialog,{...base,status:'SUCCESS'})
assert.match(completed,/Archive Delivery/); assert.match(completed,/historical processing, quality, and lineage/)
async function run(){
  let calls=[]
  global.fetch=async(url,options)=>{calls.push([url,JSON.parse(options.body)]);return{ok:true,json:async()=>({upload_request_id:77,workspace_id:10,dataset_id:20,archived_at:'2026-10-06'})}}
  await archiveDelivery('10','20',77); assert.equal(calls.length,1); assert.deepEqual(calls[0][1],{workspace_id:10,dataset_id:20}); assert.ok(calls[0][0].endsWith('/files/uploads/77/archive'))
  global.fetch=async()=>({ok:true,json:async()=>({upload_request_id:77,workspace_id:11,dataset_id:20,archived_at:'today'})})
  await assert.rejects(archiveDelivery('10','20',77),/response mismatch/)
  global.fetch=async()=>({ok:false,status:409,json:async()=>({detail:'private'})})
  await assert.rejects(archiveDelivery('10','20',77),/currently processing/)
  console.log('Delivery removal/archival policy, confirmation, running-state guard, responsive structure and scoped API checks passed.')
}
run().catch(error=>{console.error(error);process.exitCode=1})
