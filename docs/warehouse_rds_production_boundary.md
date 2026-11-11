# Warehouse deployment boundary: local PostgreSQL vs Amazon RDS for PostgreSQL

## Authoritative architecture

- **Local development:** PostgreSQL 18 on Windows, database `ai_data_workspace`. Canonical users, sessions, workspace 18, model versions, publications, and serving receipts live in `public`. Isolated `datarise_warehouse_dev` contains query migrations but no workspace records.
- **Production:** **Amazon RDS for PostgreSQL** is the relational database hosting service; it is not a different SQL engine. Do **not** infer that local `postgres` superuser bootstrap works on RDS.
- **Analytics:** Google BigQuery is the warehouse compute/results provider; the three-principal cloud-only test passed using a named destination, a verified query job, independent metadata attestation and independent result-table reader. This does **not** establish authenticated Datarise API/UI execution.
- **AWS:** AWS deployment stays separate until the authenticated warehouse flow passes locally.

## Identity provisioning completed locally

`tools/warehouse_provision_roles.py --apply` was reported successful: all eight identity objects were created/verified; no passwords, table/schema privileges or process credentials were granted. The role set includes a NOLOGIN owner plus seven runtime identities. The tool's administrative precondition specifically requires a true local superuser and is not a production RDS migration.

## Production-specific constraints

Amazon RDS for PostgreSQL's default `postgres` master account has `rds_superuser`, `CREATEROLE` and `CREATEDB`, but **not** the PostgreSQL `SUPERUSER` attribute. An RDS-specific bootstrap must be separately reviewed, run using an authorized RDS administrative/migration account, and explicitly validate intended database/schema ownership, password or IAM-auth setup, TLS, least-privilege grants, and no owner-role membership for runtime identities.

Do not use master credentials in the running API or workers. Store distinct runtime credentials in AWS Secrets Manager or configure approved IAM DB authentication and dedicated application roles. IAM DB authentication introduces membership of `rds_iam`; the current restricted connection factory forbids **all** memberships. That is an unresolved design choice, not a reason to bypass the factory.

The query protocol's schema separation is unresolved. A blind `search_path=public` or copying users/sessions into the isolated schema would break the reviewed trust boundary. PostgreSQL foreign keys can reference explicit cross-schema tables, but the entire trigger/function dependency and permission closure must be reviewed before connecting isolated query state to `public` identity. Do not replace existing auth tables or migrate existing workspace data as a workaround.

References:
- https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Appendix.PostgreSQL.CommonDBATasks.Roles.rds_superuser.html
- https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.MasterAccounts.html
- https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.IAMDBAuth.DBAccounts.html

## Acceptance definition

1. Authenticated session and workspace 18 owner checked against canonical application records.
2. Dedicated role login and full SQL grants/function/trigger dependency closure verified.
3. Query pinned to one published serving materialization, bounded policy and ledger reservation.
4. Independent BigQuery send-once, attestation, accounting and result reading through separate runtime identities/processes.
5. Actual API/UI query returns bounded rows with denied cross-user access and retry/failure safety.
6. Only then approve RDS production architecture and AWS deployment.

No production RDS resources, security groups, roles, credentials, or database objects are changed by this document.
