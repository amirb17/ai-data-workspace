-- Task 21G.6: provision ONLY the five Task 20 PostgreSQL identities.
-- REVIEW ONLY: do not run against RDS or the existing development DB yet.
-- Run separately from migrations and deployment/task20_runtime_privileges.sql.
-- Requires a PostgreSQL administrator with permission to CREATE ROLE.
-- Cluster-wide roles; no passwords, memberships, application grants, or schema changes.
-- Existing roles are validated, NEVER altered or replaced.
-- Apply with psql -X -v ON_ERROR_STOP=1 -f <this-file> after explicit approval.

BEGIN;

DO $task21g$
DECLARE
    role_names text[] := ARRAY[
        'datarise_authority_owner',
        'datarise_api',
        'datarise_worker',
        'datarise_verifier',
        'datarise_dispatch'
    ];
    role_name text;
    other_role text;
    existing pg_roles%ROWTYPE;
    should_login boolean;
BEGIN
    -- Check all existing identities before creating anything.
    FOREACH role_name IN ARRAY role_names LOOP
        should_login := role_name <> 'datarise_authority_owner';
        SELECT * INTO existing FROM pg_roles WHERE rolname = role_name;
        IF FOUND THEN
            IF existing.rolcanlogin IS DISTINCT FROM should_login
               OR existing.rolsuper
               OR existing.rolcreatedb
               OR existing.rolcreaterole
               OR existing.rolreplication
               OR existing.rolbypassrls
               OR existing.rolinherit THEN
                RAISE EXCEPTION 'Existing Task 20 role % has unsafe attributes; review manually', role_name;
            END IF;
        END IF;
    END LOOP;

    -- Reject direct or indirect memberships among these identities, including
    -- paths granting SET ROLE or inheritable authority in modern PostgreSQL.
    FOREACH role_name IN ARRAY role_names LOOP
        FOREACH other_role IN ARRAY role_names LOOP
            IF role_name <> other_role
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name)
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = other_role) THEN
                IF pg_has_role(role_name, other_role, 'MEMBER') THEN
                    RAISE EXCEPTION 'Task 20 role % must not be a member of %', role_name, other_role;
                END IF;
            END IF;
        END LOOP;
    END LOOP;

    -- Create missing roles only. A NULL password means password authentication
    -- fails until credentials are provisioned in the approved separate step.
    FOREACH role_name IN ARRAY role_names LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            should_login := role_name <> 'datarise_authority_owner';
            EXECUTE format(
                'CREATE ROLE %I WITH %s NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD NULL',
                role_name,
                CASE WHEN should_login THEN 'LOGIN' ELSE 'NOLOGIN' END
            );
            RAISE NOTICE 'Created restricted role %', role_name;
        ELSE
            RAISE NOTICE 'Existing restricted role % validated; no changes', role_name;
        END IF;
    END LOOP;
END
$task21g$;

COMMIT;

-- SECURITY ACCEPTANCE (not performed by this script):
-- 1. Inspect pg_roles, pg_auth_members, inherited/effective permissions.
-- 2. Apply deployment/task20_runtime_privileges.sql only after migrations 0041-0043
--    and reviewed application grants, using its required psql variables.
-- 3. Provision distinct passwords securely, never in SQL files, CLI history,
--    Terraform variables/state, source control, application logs or output.
-- 4. Require TLS certificate/hostname verification for each DB client.
-- 5. Test each login's current_user/session_user and positive/negative privileges.
-- 6. Role creation alone is NOT proof of isolation or authorization to deploy.
