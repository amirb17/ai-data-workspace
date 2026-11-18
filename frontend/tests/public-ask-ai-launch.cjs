const fs = require('node:fs'); const assert=require('node:assert/strict');
const api=fs.readFileSync('app/api/auth.py','utf8');
const publicAnalytics=fs.readFileSync('frontend/src/features/workspaces/analytics/PublicWorkspaceAnalytics.tsx','utf8');
const page=fs.readFileSync('frontend/src/pages/Workspace/Analytics/WorkspaceAnalyticsPage.tsx','utf8');
assert.match(api, /'ask_ai_available': launch\.ask_ai_enabled/);
assert.match(publicAnalytics, /showAsk && <WorkspaceAsk workspaceId=\{workspaceId\}/);
assert.match(page, /config\.ask_ai_available===true/);
assert.match(page, /showAsk=\{askAvailable\}/);
console.log('Public Ask AI presentation guard passed');