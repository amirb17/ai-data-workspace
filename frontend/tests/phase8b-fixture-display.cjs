// Browser replay of real persisted API answers; no Gemini call or development DB write.
const fs = require('node:fs'), path = require('node:path')
const source = path.resolve(__dirname, '../../docs/phase8b-safe-answers.json')
if (!fs.existsSync(source)) { console.log('Phase 8B fixture display skipped: no PostgreSQL evidence'); process.exit(0) }
const evidence = JSON.parse(fs.readFileSync(source, 'utf8'))
const crossFile = path.resolve(__dirname, '../../docs/phase8b-safe-cross-answer.json')
const liveFile = path.resolve(__dirname, '../../docs/phase8b-live-safe-answer.json')
const liveCrossFile = path.resolve(__dirname, '../../docs/phase8b-live-cross-answer.json')
const live = process.env.PHASE8B_USE_LIVE_EVIDENCE === '1' && fs.existsSync(liveFile) ? JSON.parse(fs.readFileSync(liveFile, 'utf8')) : null
const liveCross = live && fs.existsSync(liveCrossFile) ? JSON.parse(fs.readFileSync(liveCrossFile, 'utf8')) : null
const liveApi = process.env.PHASE8B_LIVE_FIXTURE_API === '1'
const answers = live?.answer.status === 'ANSWERED' ? [live.answer, ...(liveCross?.answer.status === 'ANSWERED' ? [liveCross.answer] : [])] : [...evidence.answers, ...(fs.existsSync(crossFile) ? [JSON.parse(fs.readFileSync(crossFile, 'utf8'))] : [])]
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Phase 8B verified fixture replay</title></head><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import '/src/index.css';
import {WorkspaceAsk} from '/src/features/workspaces/ask/WorkspaceAsk.tsx';
const answers=${JSON.stringify(answers).replaceAll('<', '\\u003c')};
${liveApi ? "const realFetch=globalThis.fetch.bind(globalThis); globalThis.fetch=(url,options)=>realFetch(String(url).replace(/https?:\\/\\/(localhost|127\\.0\\.0\\.1):8000/, 'http://127.0.0.1:8001'), options);" : "globalThis.fetch=async(url,options={})=>({ok:true,json:async()=>String(url).endsWith('/readiness')?{workspace_id:1,ready:true,message:'Replay of verified PostgreSQL API responses; no live execution.',suggested_questions:[answers[0].question]}:String(url).endsWith('/answers')?{workspace_id:1,answers}:answers[0]});"}
createRoot(document.getElementById('root')).render(React.createElement('main',{className:'mx-auto max-w-5xl space-y-4 p-4'},React.createElement('h1',{className:'text-xl font-semibold'},'${liveApi ? 'Live isolated synthetic API verification' : 'Verified synthetic API answer replay'}'),React.createElement('p',{},'Production workspace Ask component. ${live?.answer.status === 'ANSWERED' ? 'Live Gemini supplied typed plans; the backend computed and persisted these answers. ' : ''}Answers came from disposable PostgreSQL and current checksummed Gold. ${liveApi ? 'Browser requests reach the temporary isolated fixture API; the development workspace is untouched.' : 'Browser requests replay saved responses; controls do not perform live calculations.'}'),React.createElement(WorkspaceAsk,{workspaceId:'1'})));
</script></body></html>`
fs.writeFileSync(path.resolve(__dirname, '../phase8b-fixture-preview.html'), html)
console.log('Phase 8B browser fixture replay prepared from verified API answers')
