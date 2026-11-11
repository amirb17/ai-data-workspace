-- Development-only warehouse role identities. No credentials and NO data grants.
-- Provision in ai_data_workspace with the PostgreSQL administrator.
-- This is not approval to activate the warehouse query API.
-- Deliberately separate from Task 20 processing identities.
BEGIN;
DO $warehouse_roles$
DECLARE
    wanted text[] := ARRAY[
        'datarise_wh_owner',
        'datarise_wh_control',
        'datarise_wh_submit',
        'datarise_wh_attest',
        'datarise_wh_account',
        'datarise_wh_results',
        'datarise_wh_publish',
        'datarise_wh_retention'
    ];
    candidate text;
    peer text;
    expected_login boolean;
    found_role pg_roles%ROWTYPE;
BEGIN
    IF current_database() <> 'ai_data_workspace'
       OR session_user <> 'postgres'
       OR NOT EXISTS (
            SELECT 1 FROM pg_namespace
            WHERE nspname = 'datarise_warehouse_dev'
       )
       OR NOT (SELECT rolsuper FROM pg_roles WHERE rolname = session_user)
    THEN
        RAISE EXCEPTION 'WAREHOUSE_LOCAL_DEV_ADMIN_CONTEXT_REQUIRED';
    END IF;

    -- Do not silently adopt existing similarly named accounts.
    FOREACH candidate IN ARRAY wanted LOOP
        expected_login := candidate <> 'datarise_wh_owner';
        SELECT * INTO found_role FROM pg_roles WHERE rolname=candidate;
        IF FOUND THEN
            IF found_role.rolcanlogin IS DISTINCT FROM expected_login
               OR found_role.rolsuper OR found_role.rolcreatedb
               OR found_role.rolcreaterole OR found_role.rolreplication
               OR found_role.rolbypassrls OR found_role.rolinherit THEN
                RAISE EXCEPTION 'UNSAFE_EXISTING_WAREHOUSE_ROLE: %', candidate;
            END IF;
            IF EXISTS (
               SELECT 1 FROM pg_auth_members m
                WHERE m.roleid = found_role.oid OR m.member = found_role.oid
            ) THEN
                RAISE EXCEPTION 'WAREHOUSE_ROLE_MEMBERSHIP_REQUIRES_REVIEW: %', candidate;
            END IF;
        END IF;
    END LOOP;

    FOREACH candidate IN ARRAY wanted LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=candidate) THEN
            EXECUTE format(
                'CREATE ROLE %I %s NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD NULL',
                candidate,
                CASE WHEN candidate = 'datarise_wh_owner'
                    THEN 'NOLOGIN' ELSE 'LOGIN' END
            );
        END IF;
        -- Remove direct grants. PostgreSQL may still grant CONNECT to
        -- PUBLIC; no warehouse schema/table access is granted by this file.
        EXECUTE format('REVOKE ALL ON DATABASE ai_data_workspace FROM %I',candidate);
        EXECUTE format('REVOKE ALL ON SCHEMA datarise_warehouse_dev FROM %I',candidate);
    END LOOP;
END
$warehouse_roles$;
COMMIT;

-- No object ownership changes. No passwords or application grants.
-- No cloud permissions. Database CONNECT may remain available through PUBLIC,
-- but no usage of datarise_warehouse_dev is granted to these roles.
