// Existing feature tests model the explicitly configured development principal.
// Auth tests run separately in session mode and exercise cookies/CSRF.
const fs=require('node:fs'),ts=require('typescript')
require.extensions['.ts']=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,file)
require('../src/services/api/client.ts').setIdentityMode('development')
