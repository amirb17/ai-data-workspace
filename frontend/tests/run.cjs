const {spawnSync}=require('node:child_process'),fs=require('node:fs'),path=require('node:path')
const files=fs.readdirSync(__dirname).filter(name=>name.endsWith('.cjs')&&!['run.cjs','legacy-api-fixture.cjs'].includes(name))
let failures=0
for(const file of files){const args=file==='authentication.cjs'?[]:['-r',path.join(__dirname,'legacy-api-fixture.cjs')];const result=spawnSync(process.execPath,[...args,path.join(__dirname,file)],{encoding:'utf8',cwd:path.dirname(__dirname)});if(result.status!==0){failures++;console.error(file+' FAILED\n'+result.stdout+result.stderr)}else console.log(file+' passed')}
console.log(`${files.length-failures}/${files.length} frontend lightweight scripts passed`)
if(failures)process.exitCode=1
