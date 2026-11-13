# DataRise AWS public CSV upload rollout (reviewed switch, default OFF)

## Scope
Reuse the existing authenticated API `POST /files/initiate` → presigned S3 `PUT`
→ `POST /files/complete`. Completion keeps the upload's database status
`UPLOADED`. It does not dispatch processing, run Bronze/Silver/Gold, grant
warehouse access, or archive delivery records.

Only explicit `DATARISE_PUBLIC_CSV_UPLOAD=true` allows those two API routes.
Only an API task with a dedicated ECS **task role** (not its execution role)
can access the approved existing bucket. S3 IAM object permissions are scoped
to `staging/user-*/*` and `raw/*`. No S3 public access is required.

## Prerequisites and verification
1. Check the existing bucket's AWS account/region, Public Access Block,
   default encryption, versioning, lifecycle and bucket policy. The current
   Terraform toggle requires the bucket and ECS in the same region.
2. Review its existing CORS rules before making any edits. Required browser
   CORS on the S3 bucket: exact origin
   `https://feature-warehouse-model-ux.d2bhduxm4lvbct.amplifyapp.com`,
   method `PUT` and header `Content-Type`. Do not make the bucket public;
   CORS does not grant object authorization.
3. On private RDS `aiworkspace`, apply
   `deployment/operations/sql/grant_public_csv_upload_api.sql` as
   `workspace_admin`, with the approved SSM tunnel open. This grants
   SELECT/INSERT/UPDATE on `upload_requests`, SELECT/INSERT on
   `physical_files` and USAGE on their identity sequences.
4. Run focused backend and frontend tests and `terraform validate`.
5. Review `api-ecs-rollout.tfvars`. Enable
   `enable_api_s3_csv_upload=true` to provision the task role, then set
   `api_s3_csv_public_live=true` only after the full preflight, and set `api_s3_upload_bucket` to
   the reviewed existing bucket and `api_s3_upload_region` to its region.
   Existing auth, SMTP, workspace creation and CloudFront flags stay enabled.
6. Build the new backend from the release commit, push to ECR and pin its
   **immutable digest** as `api_image_digest`. Review Terraform plan; approve
   only a dedicated task role/policy and ECS revision/service update. No
   changes to RDS, CloudFront, ALB, S3 bucket or worker infrastructure.
7. Confirm ECS is healthy and Amplify has deployed the matching frontend.
   `GET /auth/config` must report `csv_upload_available=true`.
8. As an authenticated user, upload a tiny CSV to an owned dataset. Confirm
   presigned PUT succeeds from the browser, `/files/complete` returns
   `COMPLETED`, and the dataset Files page lists exactly one new delivery
   with `UPLOADED` / `READY_TO_PROCESS` status. Repeated complete of the
   same upload ID must reuse its persisted completion result. Test a duplicate
   upload, mismatched workspace, file too large and disallowed content type.
   Check that /files/upload, processing/dispatch and archive remain blocked.
9. To disable public uploads without destroying the IAM role, set
   `api_s3_csv_public_live=false` and deploy an updated ECS task revision.
   For code rollback restore the prior immutable ECS image/revision. Retain
   S3 objects and database delivery records for audit. Do not change
   `enable_api_s3_csv_upload` to false until IAM decommission is approved.

## Deployment cautions
- S3 CORS is not created by Terraform here, since the bucket is pre-existing;
  modifying CORS blindly could remove other legitimate clients.
- Presigned PUT URLs are bearer capabilities for one unique staging key.
  Never log or paste presigned URLs or credentials.
- Ingestion is limited to `text/csv` with declared size 1 byte–5 MiB;
  S3 size and MIME are checked again during completion.
- Workspace/dataset ownership is enforced using the authenticated user,
  never trusting a browser-supplied owner.
- Existing uploader UI has local schema/compatibility inspection;
  backend acceptance does not imply processing completion.
