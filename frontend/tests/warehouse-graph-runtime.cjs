const assert=require('node:assert/strict')
const fs=require('node:fs')
const ts=require('typescript')
const Module=require('node:module')
const path=require('node:path')

const file=path.join(__dirname,'../src/features/workspaces/model/warehouseGraphView.ts')
const compiled=ts.transpileModule(fs.readFileSync(file,'utf8'),{
  fileName:file,
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
}).outputText
const loaded=new Module(file,module)
loaded.filename=file
loaded.paths=module.paths
loaded._compile(compiled,file)
const {currentWarehouseRelationships,warehouseGraphElements}=loaded.exports

const model=(id,version,type)=>({
  model_id:id,model_version_id:version,model_type:type,name:`Model ${id}`,version_number:1,
})
const fact=model(10,101,'FACT')
const dimension=model(20,202,'DIMENSION')
const active=[fact,dimension]
const relation={
  relationship_id:7,workspace_id:5,fact_model_id:10,fact_model_version_id:101,
  dimension_model_id:20,dimension_model_version_id:202,
  temporal_policy:'EVENT_TIME_AS_OF',name:'Sale at purchase'
}
assert.equal(currentWarehouseRelationships(5,active,[relation]).length,1)
const matching=currentWarehouseRelationships(5,active,[relation])
const graph=warehouseGraphElements(active,matching)
assert.equal(graph.nodes.length,2)
assert.equal(graph.edges.length,1)
assert.equal(graph.edges[0].source,'10')
assert.equal(graph.edges[0].target,'20')
assert.match(graph.edges[0].label,/as-of/)
assert.ok(graph.nodes.find(n=>n.id==='20').position.x>graph.nodes.find(n=>n.id==='10').position.x)

for(const invalid of [
  {...relation,workspace_id:6},
  {...relation,fact_model_version_id:999},
  {...relation,dimension_model_version_id:999},
  {...relation,dimension_model_id:777},
]) {
  const edges=currentWarehouseRelationships(5,active,[invalid])
  assert.equal(edges.length,0,'A foreign, old or unknown relationship must not render')
  assert.equal(warehouseGraphElements(active,edges).edges.length,0)
}
assert.equal(currentWarehouseRelationships(5,[dimension],[relation]).length,0,'Archived fact is absent from catalog')
assert.equal(currentWarehouseRelationships(5,[fact],[relation]).length,0,'Archived dimension is absent from catalog')
assert.equal(currentWarehouseRelationships(5,[model(10,999,'FACT'),dimension],[relation]).length,0)
assert.equal(currentWarehouseRelationships(5,[model(10,101,'DIMENSION'),dimension],[relation]).length,0)
assert.deepEqual(warehouseGraphElements([dimension,fact],matching).nodes.map(n=>n.position),
  warehouseGraphElements([fact,dimension],matching).nodes.reverse().map(n=>n.position))
console.log('Warehouse graph runtime: current approved edge, model selection, foreign scope, superseded and archived versions passed')
