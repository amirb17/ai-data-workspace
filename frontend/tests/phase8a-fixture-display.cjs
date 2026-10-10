// Render the production component using an actual isolated PostgreSQL API read.
const assert=require('node:assert/strict'),React=require('react'),fs=require('node:fs'),path=require('node:path'),ts=require('typescript')
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
const evidenceFile=process.argv[2] || path.resolve(__dirname,'../../docs/phase8a-safe-fixture.json')
if(!fs.existsSync(evidenceFile)){console.log('Fixture display skipped: run PHASE8A_WRITE_EVIDENCE=1 database fixture first');process.exit(0)}
const evidence=JSON.parse(fs.readFileSync(evidenceFile,'utf8')),data=evidence.api_read
const {MemoryRouter}=require('react-router-dom'),{renderToStaticMarkup}=require('react-dom/server')
const {WorkspaceAnalyticsContent}=require('../src/pages/Workspace/Analytics/WorkspaceAnalyticsPage.tsx')
assert.equal(data.status,'FRESH');assert.equal(data.metrics_fresh,2)
assert.ok(data.metrics.every(m=>m.output.value===2))
const html=renderToStaticMarkup(React.createElement(MemoryRouter,null,React.createElement(WorkspaceAnalyticsContent,{data,busy:false,refresh:()=>{},retry:()=>{}})))
for(const text of ['Record count','Distinct id count','Ready automatically','Review is optional','Current'])assert.ok(html.includes(text),text)
assert.ok(!html.includes('Review Metric')&&!html.includes('Approve')&&!html.includes('Needs definition'))
assert.equal((html.match(/class="text-3xl font-semibold">2<\/p>/g)||[]).length,2,'Both real metric values rendered')
const document='<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Phase 8A isolated execution evidence</title><style>body{font:16px system-ui;margin:2rem;max-width:1100px}section>div{margin:1rem 0}article{border:1px solid #ccc;padding:1rem;margin:1rem 0}button{padding:.7rem}h3{margin-bottom:.5rem}</style><h1>Isolated synthetic fixture — real API result</h1><p>Production WorkspaceAnalyticsContent rendered from PostgreSQL API evidence. Controls in this saved static evidence are inert.</p>'+html+'</html>'
fs.writeFileSync(path.resolve(__dirname,'../../docs/phase8a-safe-fixture.html'),document)
console.log('Real PostgreSQL API → production frontend: both numeric values 2, fresh, automatic/optional labels, no approval dialog')
