# 414G — warehouse query result-readiness UI

Local backend gate verified on 2026-11-11: **6 focused / 13 combined
PostgreSQL tests passed** after fixing test discovery for lazy router
registration. Authlib emits one deprecation warning unrelated to this
feature.

This incremental UI introduces an internal query status panel on the
Warehouse Models page. The panel accepts an original 32-character attempt
handle, makes a single authenticated GET to the existing
`/workspaces/{workspace_id}/warehouse/queries/{attempt_handle}/result-readiness`
endpoint, checks its exact non-fetching response contract, and displays
either readiness metadata or safe 4xx/409 feedback. It aborts in-flight
requests when the handle changes or the component unmounts.

The UI never runs SQL, fetches rows, displays provider job IDs, shows
opaque provider cursors, or overrides the warehouse release NO-GO state.
The existing dataset/Gold SQL page remains unchanged.

Frontend acceptance commands:
```powershell
cd frontend
npm run build
npm run lint
npm test
cd ..
```
No frontend commands were run by the assistant. Backend unit/integration
checks were previously run by the user.

Next: review live BigQuery result adapter and security acceptance before
exposing a warehouse result-fetch endpoint.
