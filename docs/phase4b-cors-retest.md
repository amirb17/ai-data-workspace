# Phase 4B retest after S3 CORS configuration

Result: Phase 4B is ready for approval within its defined frontend/local-cache scope.
The previous S3 CORS blocker is resolved: actual browser PUT and backend completion succeeded.
No application or backend code changes were required during this retest.

## Live browser/API results

| Case | Result |
| --- | --- |
| Backend identity and scoped routes | Passed using development user #1 and PostgreSQL workspace/dataset IDs |
| MATCH CSV | Dataset #9: upload #32 completed, physical file #22 reused, one READY_TO_PROCESS batch created |
| Refresh persistence | File and batch #32 survived reload |
| Same-dataset duplicate bytes | Early duplicate notice; no additional acceptance/batch |
| Missing required column | BREAKING; Accept disabled |
| Likely wrong dataset | WRONG_DATASET_LIKELY; Accept disabled |
| ALLOW_ADDITIVE extra column | WARNING accepted; upload #33 completed, file #23, batch #33, 1 row/3 columns |
| STRICT extra column | BREAKING; Accept disabled; original ALLOW_ADDITIVE policy restored afterward |
| Repeated backend completion | Uploads #32/#33 returned identical results on repeated calls |
| NO_CONTRACT first-file flow | Created backend dataset #10; saved initial contract and accepted the same selected file without choosing it again |
| Cross-dataset canonical reuse | Dataset #10 upload/batch #34 reused physical file #22; 1 row/2 columns |
| Dataset isolation | Dataset #10 initially empty; its batch #34 never appeared in dataset #9, which retained only #32/#33 |
| Workspace isolation | Workspace #7/dataset #8 displayed no test files or batches |
| Invalid workspace/dataset relationship | Workspace #7/dataset #9 rejected by backend; no Files page mounted |
| Processing boundary | All new batches READY_TO_PROCESS; outcomes unavailable; no processing execution |

## Automated validation

- npm run lint: passed, no warnings/errors.
- npm run build: passed.
- node tests/backend-integration.cjs: passed.
- node tests/contracts.cjs: passed.
- node tests/ingestion-batches.cjs: passed.
- node tests/file-content-duplicates.cjs: passed.
- git diff --check: passed.

The backend-integration suite additionally covers failed initiate/PUT/complete,
completion retry without another initiate or PUT, repeated-click guards, failed
local writes, route disposal, StrictMode replay, MIME, ownership mapping, and
workspace/dataset/user/API namespace isolation. These failure cases were tested
with deterministic adapters; no live S3 outage or completion failure was injected
in this retest. Backend code was unchanged, so backend pytest was not rerun.

## Evidence and limitations

Screenshot: phase4b-cors-retest.png shows the two persisted dataset #9 batches.
Existing workspace #8 and dataset #9 were reused; test dataset #10 and uploads
#32/#33/#34 remain for inspection. Scratch warning/wrong/strict fixtures were placed
in the system temporary directory. No processing, commits, or Phase 5 work occurred.

Contracts, file metadata caches and batches remain local to this browser. There is
still no backend file-list endpoint, and an in-progress upload cannot be recovered
by this UI after closing the dialog or refreshing. These are documented Phase 4B
prototype limitations, not newly discovered CORS blockers.
