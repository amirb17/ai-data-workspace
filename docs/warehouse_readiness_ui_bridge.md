# Warehouse readiness bridge — non-executing analyst UI

B12 verified locally: 3 focused / 18 combined PostgreSQL tests passed.
The remaining security acceptance work requires real authenticated service roles,
GCP IAM, physical publication retention and execution cost/recovery checks.

This change exposes the outstanding release blockers in the existing **owned**
warehouse catalog response. The React warehouse model page renders them beside
the approved FACT/DIMENSION explorer and relationship graph.

This is **not** a replacement for real readiness attestation: the response is
explicitly hard-coded to REVIEW_REQUIRED and cannot become authorized through
environment flags, user input, a resource-registration count, or static UI
state. The warehouse SQL editor remains on the older dataset/Gold engine.
No new execution route, cloud SDK call, migration, IAM change or deletion
permission is introduced.

Test locally:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_api_integration.py -q --tb=short -x
cd frontend
npm test
npm run build
npm run lint
cd ..
```

Later integration must replace the static blockers with independently
verified serving, role and provider evidence, never only a client-side switch.
The UI must not advertise queryability before that end-to-end acceptance.
