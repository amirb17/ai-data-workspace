
-- Task 21H.158E2
-- Immutable Sydney production approval records.
-- This migration does NOT enable Sydney resource registration.

CREATE TABLE warehouse_sydney_approvals (
    approval_id BIGSERIAL PRIMARY KEY,

    workspace_id BIGINT NOT NULL
        REFERENCES workspaces(workspace_id),

    project TEXT NOT NULL,
    aws_region TEXT NOT NULL
        DEFAULT 'ap-southeast-2',
    location TEXT NOT NULL
        DEFAULT 'australia-southeast1',

    loader_identity TEXT NOT NULL,
    query_identity TEXT NOT NULL,

    approval_revision INTEGER NOT NULL
        CHECK (approval_revision > 0),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT sydney_approval_workspace_revision_unique
        UNIQUE (workspace_id, approval_revision),

    CONSTRAINT sydney_approval_project_valid
        CHECK (
            project ~ '^[a-z][a-z0-9-]{4,28}[a-z0-9]$'
        ),

    CONSTRAINT sydney_approval_regions_valid
        CHECK (
            aws_region = 'ap-southeast-2'
            AND location = 'australia-southeast1'
        ),


CONSTRAINT sydney_approval_identities_valid
    CHECK (
        loader_identity ~ (
            '^[a-z][a-z0-9-]{4,28}[a-z0-9]@'
            || project ||
            '[.]iam[.]gserviceaccount[.]com$'
        )
        AND query_identity ~ (
            '^[a-z][a-z0-9-]{4,28}[a-z0-9]@'
            || project ||
            '[.]iam[.]gserviceaccount[.]com$'
        )
    ),


    CONSTRAINT sydney_approval_distinct_identities
        CHECK (loader_identity <> query_identity),

    CONSTRAINT sydney_approval_project_identities
        CHECK (
            loader_identity LIKE '%@' || project ||
                '.iam.gserviceaccount.com'
            AND query_identity LIKE '%@' || project ||
                '.iam.gserviceaccount.com'
        )
);

CREATE FUNCTION warehouse_sydney_approval_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'Sydney production approval history is immutable';
END;
$$;

CREATE TRIGGER warehouse_sydney_approval_history
BEFORE UPDATE OR DELETE
ON warehouse_sydney_approvals
FOR EACH ROW
EXECUTE FUNCTION warehouse_sydney_approval_immutable();

CREATE TRIGGER warehouse_sydney_approvals_no_truncate
BEFORE TRUNCATE
ON warehouse_sydney_approvals
FOR EACH STATEMENT
EXECUTE FUNCTION warehouse_no_truncate();

REVOKE ALL ON warehouse_sydney_approvals FROM PUBLIC;
REVOKE ALL ON SEQUENCE warehouse_sydney_approvals_approval_id_seq
FROM PUBLIC;

-- Task 21H.158E2 / E2.2D.1
-- Bind warehouse resources to immutable Sydney approvals.
-- Existing resources remain unchanged.

ALTER TABLE warehouse_sydney_approvals
ADD CONSTRAINT sydney_approval_workspace_id_unique
UNIQUE (workspace_id, approval_id);

ALTER TABLE warehouse_resources
ADD COLUMN sydney_approval_id BIGINT;

ALTER TABLE warehouse_resources
ADD CONSTRAINT warehouse_resource_sydney_approval_fk
FOREIGN KEY (workspace_id, sydney_approval_id)
REFERENCES warehouse_sydney_approvals (
    workspace_id,
    approval_id
)
ON UPDATE RESTRICT
ON DELETE RESTRICT;

-- Task 21H.158E2 / E2.2D.2
-- Fail-closed Sydney resource registration boundary.

CREATE FUNCTION warehouse_sydney_resource_registration_guard()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path FROM CURRENT
AS $$
DECLARE
    approval warehouse_sydney_approvals%ROWTYPE;
BEGIN
    IF NEW.environment = 'production'
       AND NEW.location = 'australia-southeast1'
    THEN
        IF NEW.sydney_approval_id IS NULL THEN
            RAISE EXCEPTION
                'Sydney production resource requires reviewed approval';
        END IF;

        SELECT *
        INTO approval
        FROM warehouse_sydney_approvals
        WHERE approval_id = NEW.sydney_approval_id
          AND workspace_id = NEW.workspace_id
        FOR SHARE;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Sydney production approval not found in workspace';
        END IF;

        IF NEW.physical_reference->>'project'
              IS DISTINCT FROM approval.project
           OR NEW.physical_reference->>'dataset'
              IS DISTINCT FROM
                 ('dr_prod_w' || NEW.workspace_id::text)
           OR NEW.physical_reference->>'table'
              IS DISTINCT FROM
                 (
                     'm' || NEW.warehouse_model_id::text
                     || '_v' ||
                     NEW.warehouse_model_version_id::text
                     || '_g' || NEW.generation::text
                 )
        THEN
            RAISE EXCEPTION
                'Sydney physical resource identity does not match approval';
        END IF;
    ELSIF NEW.sydney_approval_id IS NOT NULL THEN
        RAISE EXCEPTION
            'Sydney approval cannot authorize non-Sydney resource';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER warehouse_sydney_resource_registration
BEFORE INSERT ON warehouse_resources
FOR EACH ROW
EXECUTE FUNCTION warehouse_sydney_resource_registration_guard();

-- Freeze the trusted schema for the privileged trigger.
-- pg_temp is last to prevent temporary-object shadowing.

DO $$
DECLARE
    trusted_schema TEXT := current_schema();
BEGIN
    IF trusted_schema IS NULL
       OR trusted_schema = 'public'
    THEN
        RAISE EXCEPTION
            'Sydney approval migration requires an explicitly trusted non-public schema';
    END IF;

    EXECUTE format(
        'ALTER FUNCTION %I.warehouse_sydney_resource_registration_guard() '
        || 'SET search_path = pg_catalog, %I, pg_temp',
        trusted_schema,
        trusted_schema
    );

    EXECUTE format(
        'REVOKE ALL ON FUNCTION '
        || '%I.warehouse_sydney_resource_registration_guard() FROM PUBLIC',
        trusted_schema
    );
END;
$$;

-- Retain Mumbai compatibility while admitting reviewed Sydney bindings.
-- The dedicated insert guard enforces approval and exact physical identity.

ALTER TABLE warehouse_resources
DROP CONSTRAINT warehouse_resources_check;

ALTER TABLE warehouse_resources
ADD CONSTRAINT warehouse_resources_check
CHECK (
    environment <> 'production'
    OR location IN (
        'asia-south1',
        'australia-southeast1'
    )
);
