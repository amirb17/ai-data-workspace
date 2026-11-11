const assert=require('node:assert/strict')
const fs=require('node:fs')
const path=require('node:path')
const ts=require('typescript')

for (const relative of [
  'src/features/workspaces/model/WarehouseModelPage.tsx',
  'src/features/workspaces/model/WarehouseGraph.tsx',
  'src/features/workspaces/model/warehouseGraphView.ts',
  'src/features/workspaces/model/WorkspaceModelPage.tsx',
  'src/services/api/warehouseCatalog.ts',
]) {
  const source=fs.readFileSync(path.join(__dirname,'..',relative),'utf8')
  const result=ts.transpileModule(source,{
    fileName:relative,
    reportDiagnostics:true,
    compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX},
  })
  assert.equal(result.diagnostics?.filter(d=>d.category===ts.DiagnosticCategory.Error).length||0,0,relative)
}

const ui=fs.readFileSync(path.join(__dirname,'../src/features/workspaces/model/WorkspaceModelPage.tsx'),'utf8')
const warehouse=fs.readFileSync(path.join(__dirname,'../src/features/workspaces/model/WarehouseModelPage.tsx'),'utf8')
const api=fs.readFileSync(path.join(__dirname,'../src/services/api/warehouseCatalog.ts'),'utf8')
assert.match(ui,/Dataset relationships/)
assert.match(ui,/Data warehouse/)
assert.match(ui,/surface==='dataset'/)
assert.match(ui,/<DatasetModelPage/)
assert.match(ui,/<WarehouseModelPage/)
assert.match(warehouse,/No current reviewed FACT-to-DIMENSION relationships/)
assert.match(warehouse,/Warehouse SQL remains disabled/)
assert.match(warehouse,/Serving not verified/)
assert.match(warehouse,/Warehouse release readiness/)
assert.match(warehouse,/catalog\.readiness\.blockers\.map/)
assert.match(api,/warehouse_sql_enabled: false/)
assert.match(warehouse,/selected\.columns\.map/)
assert.match(warehouse,/selected\.sources\.map/)
assert.match(warehouse,/getWarehouseRelationships/)
assert.match(warehouse,/<WarehouseGraph/)
assert.match(warehouse,/currentWarehouseRelationships/)
assert.match(warehouse,/Selected warehouse relationship/)
assert.match(api,/\/warehouse\/catalog/)
assert.match(api,/result\.workspace_id !== workspaceId/)
assert.match(api,/queryable: false/)
console.log('Warehouse model switch, typed catalog, guarded statuses and dataset graph preservation checks passed')

const graph=fs.readFileSync(path.join(__dirname,'../src/features/workspaces/model/WarehouseGraph.tsx'),'utf8')
const view=fs.readFileSync(path.join(__dirname,'../src/features/workspaces/model/warehouseGraphView.ts'),'utf8')
assert.match(graph,/onEdgeClick/)
assert.match(graph,/onNodeClick/)
assert.match(graph,/nodesConnectable={false}/)
assert.match(graph,/onKeyDownCapture/)
assert.match(view,/relation\.workspace_id===workspaceId/)
assert.match(view,/fact\.model_version_id===relation\.fact_model_version_id/)
assert.match(view,/dimension\.model_version_id===relation\.dimension_model_version_id/)
assert.match(view,/relationships\.filter/)
