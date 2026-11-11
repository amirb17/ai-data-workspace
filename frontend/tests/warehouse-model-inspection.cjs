const assert=require('node:assert/strict')
const fs=require('node:fs')
const path=require('node:path')
const Module=require('node:module')
const ts=require('typescript')
const file=path.join(__dirname,'../src/features/workspaces/model/warehouseModelInspection.ts')
const compiled=ts.transpileModule(fs.readFileSync(file,'utf8'),{
  fileName:file, compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
}).outputText
const loaded=new Module(file,module)
loaded.filename=file
loaded.paths=module.paths
loaded._compile(compiled,file)
const {warehouseModelInspection,warehouseModelInspectionJson}=loaded.exports
const model={
  model_id:5,model_version_id:12,name:'orders_fact',model_type:'FACT',grain:'One row per order',
  scd_type:null,columns:[{name:'order_id',type:'INTEGER',role:'IDENTITY'}],
  sources:[{dataset_id:2,dataset_name:'orders',purpose:'PRIMARY'}],
}
const current={relationship_id:3,name:'Orders to customers',fact_model_id:5,
  fact_model_version_id:12,dimension_model_id:9,dimension_model_version_id:11,
  key_pairs:[{fact_column:'customer_id',dimension_column:'id'}],temporal_policy:'CURRENT_MEMBER'}
const stale={...current,relationship_id:4,fact_model_version_id:3}
const unrelated={...current,relationship_id:5,fact_model_id:999,fact_model_version_id:888}
const v=warehouseModelInspection(model,[current])
assert.equal(v.kind,'WAREHOUSE_MODEL_INSPECTION')
assert.equal(v.execution_authorized,false)
assert.equal(v.model_version_id,12)
assert.equal(v.columns[0].name,'order_id')
assert.equal(v.sources[0].name,'orders')
assert.deepEqual(v.reviewed_relationships.map(x=>x.id),[3])
const parsed=JSON.parse(warehouseModelInspectionJson(model,[current]))
assert.deepEqual(parsed,v)
assert.doesNotMatch(JSON.stringify(v),/SELECT\s|provider_job_id|BigQueryTarget/)
console.log('Warehouse model inspection exports reviewed columns, sources and links, no query authorization')
