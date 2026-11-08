// Test-only guard: legacy fixture renderers must not overwrite historical work.
// Rendering/assertions still run; changed/new HTML is retained in an OS temp dir.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os')
const protectedPaths=new Set([
 path.resolve(__dirname,'../../docs/phase8a-safe-fixture.html'),
 path.resolve(__dirname,'../phase8b-fixture-preview.html'),
 path.resolve(__dirname,'../phase8c-fixture-preview.html'),
])
const write=fs.writeFileSync
let evidence
fs.writeFileSync=function(target,data,options){
 if(typeof target==='string'&&protectedPaths.has(path.resolve(target))){
  const bytes=typeof data==='string'?Buffer.from(data,typeof options==='string'?options:options?.encoding??'utf8'):Buffer.from(data)
  if(fs.existsSync(target)&&fs.readFileSync(target).equals(bytes))return
  evidence??=fs.mkdtempSync(path.join(os.tmpdir(),'datarise-frontend-evidence-'))
  console.log('Historical HTML fixture retained; generated evidence redirected to a temporary directory.')
  return write.call(fs,path.join(evidence,path.basename(target)),bytes)
 }
 return write.call(fs,target,data,options)
}
