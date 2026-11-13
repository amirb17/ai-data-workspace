// Guard against the empty-workspace Overview accidentally calling the
// public-release-blocked analytics endpoint (403 misleadingly looks like auth).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const file = fs.readFileSync(path.join(__dirname,'../src/features/workspaces/useWorkspacePreparation.ts'),'utf8')
assert.ok(file.includes('if (active.length === 0)'))
assert.ok(file.includes('readiness: "NO_DATA"'))
assert.ok(file.indexOf('if (active.length === 0)') < file.indexOf('await getWorkspaceAnalytics(workspace, signal)'))
assert.ok(file.includes('return {rows, analytics}'))
console.log('Empty workspace Overview skips blocked analytics request.')
