// Offline release regression: CSV upload is explicitly server-opted-in.
const fs=require('node:fs'), assert=require('node:assert/strict'), path=require('node:path');
const read=p=>fs.readFileSync(path.join(__dirname,'../src',p),'utf8');
const layout=read('components/layout/PublicLaunchPolicy.tsx');
const files=read('pages/Dataset/Files/DatasetFilesPage.tsx');
const api=read('services/api/auth.ts');
assert.ok(layout.includes("value.csv_upload_available===true"));
assert.ok(layout.includes('UploadAllowedContext.Provider value={status.upload}'));
assert.ok(files.includes('const uploadAllowed=usePublicUploadAllowed()'));
assert.ok(files.includes('const readOnly=publicReadOnly && !uploadAllowed'));
assert.ok(files.includes('readOnly={publicReadOnly}')); // archive remains disabled
assert.ok(api.includes('csv_upload_available?:boolean'));
console.log('Public upload remains default-off and separate from archive/processing.');
