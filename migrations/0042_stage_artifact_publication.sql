
-- Processing artifact publication foundation.
-- Additive metadata only; existing stage readers/writers unchanged.
-- Not approved for application until repository and tests are reviewed.

BEGIN;

-- Required for composite source identity validation.
ALTER TABLE dataset_version_files
    ADD CONSTRAINT uq_dvf_file_identity
    UNIQUE (dataset_version_file_id, file_id);

CREATE TABLE processing_stage_intents (
    intent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    job_id UUID NOT NULL REFERENCES processing_jobs(job_id),
    lease_token UUID NOT NULL,

    stage VARCHAR(16) NOT NULL
        CHECK (stage IN ('BRONZE', 'SILVER', 'GOLD')),

    file_id BIGINT NOT NULL REFERENCES physical_files(file_id),
    dataset_version_file_id BIGINT,

    input_fingerprint CHAR(64) NOT NULL
        CHECK (input_fingerprint ~ '^[a-f0-9]{64}$'),

    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_stage_intent_execution_scope
    UNIQUE NULLS NOT DISTINCT
    (job_id, stage, lease_token, file_id, dataset_version_file_id),
    UNIQUE (intent_id, job_id, lease_token),
    UNIQUE NULLS NOT DISTINCT
    (intent_id, stage, file_id, dataset_version_file_id),
    CONSTRAINT chk_stage_source_scope CHECK (
        (stage = 'BRONZE' AND dataset_version_file_id IS NULL)
        OR
        (stage IN ('SILVER', 'GOLD')
            AND dataset_version_file_id IS NOT NULL)
    ),

    CONSTRAINT fk_stage_dvf_file
        FOREIGN KEY (dataset_version_file_id, file_id)
        REFERENCES dataset_version_files(dataset_version_file_id, file_id)

);

CREATE TABLE processing_artifact_candidates (
    candidate_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    intent_id UUID NOT NULL
        REFERENCES processing_stage_intents(intent_id),

    manifest_sha256 CHAR(64) NOT NULL
        CHECK (manifest_sha256 ~ '^[a-f0-9]{64}$'),

    status VARCHAR(16) NOT NULL DEFAULT 'STAGED'
        CHECK (status IN ('STAGED', 'VERIFIED', 'RETIRED')),

    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    UNIQUE (intent_id, candidate_id),

    CHECK (status <> 'VERIFIED' OR verified_at IS NOT NULL)
);

CREATE TABLE processing_stage_receipts (
    receipt_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    intent_id UUID NOT NULL UNIQUE,
    candidate_id UUID NOT NULL UNIQUE,
    -- Pins the publication to its original execution context.
    job_id UUID NOT NULL
    REFERENCES processing_jobs(job_id),

    lease_token UUID NOT NULL,

    published_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    FOREIGN KEY (intent_id, candidate_id)
        REFERENCES processing_artifact_candidates(intent_id, candidate_id),
    CONSTRAINT fk_receipt_intent_execution
    FOREIGN KEY (intent_id, job_id, lease_token)
    REFERENCES processing_stage_intents(
        intent_id, job_id, lease_token
    ),
    stage VARCHAR(16) NOT NULL,
file_id BIGINT NOT NULL,
dataset_version_file_id BIGINT,

CONSTRAINT fk_receipt_stage_source
    FOREIGN KEY (intent_id, stage, file_id, dataset_version_file_id)
    REFERENCES processing_stage_intents
        (intent_id, stage, file_id, dataset_version_file_id),
    UNIQUE (receipt_id, candidate_id)
);

-- A stage scope has one current authoritative candidate.
-- Historical publication receipts are preserved separately.
CREATE TABLE processing_stage_heads (
    stage VARCHAR(16) NOT NULL
        CHECK (stage IN ('BRONZE', 'SILVER', 'GOLD')),

    file_id BIGINT NOT NULL
        REFERENCES physical_files(file_id),

    dataset_version_file_id BIGINT,

    receipt_id UUID NOT NULL,
    candidate_id UUID NOT NULL,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT fk_stage_head_receipt
        FOREIGN KEY (receipt_id, candidate_id)
        REFERENCES processing_stage_receipts(receipt_id, candidate_id),

    CONSTRAINT chk_stage_head_scope CHECK (
        (stage = 'BRONZE' AND dataset_version_file_id IS NULL)
        OR
        (stage IN ('SILVER', 'GOLD')
            AND dataset_version_file_id IS NOT NULL)
    ),

    CONSTRAINT fk_stage_head_dvf_file
        FOREIGN KEY (dataset_version_file_id, file_id)
        REFERENCES dataset_version_files(dataset_version_file_id, file_id),

    CONSTRAINT uq_stage_head_scope
        UNIQUE NULLS NOT DISTINCT
        (stage, file_id, dataset_version_file_id)
);
-- Published receipts must remain immutable.
CREATE FUNCTION protect_processing_stage_receipt()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Published stage receipts are immutable';
END;
$$;

CREATE TRIGGER immutable_processing_stage_receipt
BEFORE UPDATE OR DELETE ON processing_stage_receipts
FOR EACH ROW
EXECUTE FUNCTION protect_processing_stage_receipt();
-- Protect immutable candidate identity and verification evidence.

CREATE FUNCTION protect_processing_artifact_candidate()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    -- 1. Candidate history cannot be deleted.
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'Artifact candidate history cannot be deleted';
    END IF;

    -- 2. Candidate identity and manifest checksum are immutable.
    IF NEW.candidate_id IS DISTINCT FROM OLD.candidate_id
       OR NEW.intent_id IS DISTINCT FROM OLD.intent_id
       OR NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION
            'Artifact candidate identity is immutable';
    END IF;

    -- 3. Only approved lifecycle transitions are allowed:
    -- STAGED -> VERIFIED -> RETIRED.
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
            (OLD.status = 'STAGED' AND NEW.status = 'VERIFIED')
            OR
            (OLD.status = 'VERIFIED' AND NEW.status = 'RETIRED')
        ) THEN
            RAISE EXCEPTION
                'Invalid artifact candidate lifecycle transition';
        END IF;
    END IF;

    -- 4. Verification timestamp cannot be changed once recorded.
    IF OLD.verified_at IS NOT NULL
       AND NEW.verified_at IS DISTINCT FROM OLD.verified_at
    THEN
        RAISE EXCEPTION
            'Candidate verification evidence is immutable';
    END IF;

    -- 5. Verified and retired candidates require verification evidence.
    IF NEW.status IN ('VERIFIED', 'RETIRED')
       AND NEW.verified_at IS NULL
    THEN
        RAISE EXCEPTION
            'Verified candidate requires verification timestamp';
    END IF;

    -- 6. Prevent retirement of ANY previously published candidate.
    -- This protects both current and historical publications.
    -- Retirement must wait for an approved retention policy.
    IF NEW.status = 'RETIRED'
       AND OLD.status IS DISTINCT FROM 'RETIRED'
       AND EXISTS (
           SELECT 1
           FROM processing_stage_receipts
           WHERE candidate_id = OLD.candidate_id
       )
    THEN
        RAISE EXCEPTION
            'Published artifact retirement requires an approved retention policy';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER immutable_processing_artifact_candidate
BEFORE UPDATE OR DELETE ON processing_artifact_candidates
FOR EACH ROW
EXECUTE FUNCTION protect_processing_artifact_candidate();
-- Ensure an authoritative head references a receipt
-- from exactly the same stage and source scope.
CREATE FUNCTION validate_processing_stage_head_scope()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM processing_stage_receipts AS r
        JOIN processing_stage_intents AS i
            ON i.intent_id = r.intent_id
        WHERE r.receipt_id = NEW.receipt_id
          AND r.candidate_id = NEW.candidate_id
          AND i.stage = NEW.stage
          AND i.file_id = NEW.file_id
          AND i.dataset_version_file_id
              IS NOT DISTINCT FROM NEW.dataset_version_file_id
    ) THEN
        RAISE EXCEPTION
            'Publication head does not match receipt stage/source scope';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER validate_processing_stage_head_scope_trigger
BEFORE INSERT OR UPDATE ON processing_stage_heads
FOR EACH ROW
EXECUTE FUNCTION validate_processing_stage_head_scope();
COMMIT;
