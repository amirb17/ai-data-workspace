# DataRise AI Engineering Instructions

## Product

DataRise AI is a self-service data engineering and analytics platform.

Core product flow:

User
→ Workspace
→ Datasets
→ Files / ingestion batches
→ Dataset processing
→ Workspace data model
→ Workspace orchestration
→ Analytics
→ Ask Your Data

## Domain model

Use these definitions consistently:

- Workspace = business domain / orchestration boundary
- Dataset = one logical business entity/table and primary processing boundary
- File = physical uploaded source object
- Ingestion Batch = logical delivery of data into a dataset
- Dataset Contract = trusted schema, keys, required fields, and load strategy
- Data Model = relationships between datasets
- Workspace Analytics = trusted analytics built from one or more datasets

Do not treat the entire workspace as one dataset.

Do not treat a physical file as the business entity.

## Processing architecture

Dataset-level processing:
- ingestion
- schema validation
- deduplication
- data quality
- business rules
- quarantine
- incremental APPEND / UPSERT / SNAPSHOT
- Raw → Silver → Gold

Workspace-level processing:
- dependency orchestration
- PK/FK relationship validation
- cross-dataset models
- business marts
- analytics freshness
- workspace analytics

Rule:

Dataset = processing boundary.
Workspace = orchestration and analytics boundary.

## Existing backend principles

Preserve existing architecture.

Backend:
- FastAPI
- PostgreSQL control plane
- AWS S3 data plane
- private bucket
- medallion architecture
- immutable Raw
- Silver validation / cleaning
- Gold analytics outputs
- SHA-256 physical-file deduplication
- processing attempts / failure recovery
- schema fingerprints
- business rule versioning
- data-quality runs
- semantic Gold catalog
- structured analytics query engine
- Gemini is used only for planning/explanation
- AI must never receive AWS credentials, S3 paths, database secrets, or unrestricted database access

Do not rewrite existing working backend components unless required.

## Frontend

Stack:
- React
- TypeScript
- Vite
- Tailwind CSS
- React Router
- Lucide React
- Recharts
- Framer Motion

Frontend architecture should remain modular under:
- src/components
- src/features
- src/pages
- src/services
- src/types
- src/utils

Use responsive desktop + mobile layouts.

Do not introduce Bootstrap, MUI, or another UI framework.

## UI hierarchy

Main navigation:

Home
Workspaces
Processing
Analytics
Data Quality
Settings

Workspace:

Overview
Datasets
Data Model
Processing
Data Quality
Analytics

Dataset:

Overview
Files
Rules
Processing
Data Quality
Analytics
History

## Current ingestion direction

Upload flow:

Upload
→ Inspect
→ Map
→ Validate against Dataset Contract
→ Create Ingestion Batch
→ Process

CSV:
- normally one logical table

Excel:
- workbook is a container
- detect sheets / named tables
- each sheet/table may map to a separate dataset

Wrong-dataset uploads must be blocked or require mapping.

## Incremental processing

Plan for:
- APPEND
- UPSERT
- SNAPSHOT

Do not simply drop duplicate business keys.

Differentiate:
- exact physical-file duplicates
- exact record duplicates
- legitimate updates using the same business key

Support future handling for:
- late-arriving data
- schema drift
- backfills
- reruns
- idempotency
- partial failures
- quarantine
- corrections

## Engineering rules

1. Inspect existing implementation before changing code.
2. Reuse existing abstractions where possible.
3. Avoid giant components.
4. Keep domain logic outside UI components where practical.
5. Do not duplicate existing logic.
6. Maintain type safety.
7. Do not add dependencies unless clearly necessary.
8. Never expose secrets.
9. Do not delete or rewrite working backend functionality without justification.
10. Do not silently change routes or domain semantics.
11. Make small, reviewable changes.
12. Run relevant tests after changes.
13. Run frontend lint after frontend changes.
14. Preserve responsive behavior.

## Validation commands

Frontend:

cd frontend
npm run lint

Backend:

uv run pytest

Run the appropriate validation for the phase.

## Git rules

Before changing code:
- inspect git status

After changes:
- inspect git diff
- run tests/lint
- report changed files
- report test results
- do not commit unless explicitly requested

## Working style

For every task:

1. Inspect relevant existing code.
2. Briefly state implementation plan.
3. Make only changes required for the current phase.
4. Run validation.
5. Summarize:
   - changed files
   - behavior implemented
   - tests/lint result
   - known limitations
   - recommended next step