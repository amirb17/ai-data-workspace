// Regression: public release optional semantic routes must not replace an
// authenticated workspace overview with the generic sign-in error.
const assert=require('node:assert/strict')
const fs=require('node:fs')
const path=require('node:path')
const source=fs.readFileSync(path.join(__dirname,'../src/features/workspaces/useWorkspacePreparation.ts'),'utf8')
assert.match(source,/getUnderstandingReadiness/)
assert.match(source,/if \(!\(error instanceof ApiError\) \|\| error.status !== 403\) throw error/)
assert.match(source,/understanding: Understanding \| null = null/)
assert.match(source,/await Promise\.all\(\[datasetProcessing/)
assert.match(source,/getAnalyticsReadiness\(workspace/)
console.log('Overview safely handles unavailable optional semantic endpoints while retaining authorized processing and Analytics.')
