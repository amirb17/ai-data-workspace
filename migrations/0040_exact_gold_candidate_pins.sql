-- Exact Gold V2 validated-candidate metadata.
-- This migration does NOT enable V2 publication.

ALTER TABLE dataset_gold_runs
ADD CONSTRAINT dataset_gold_v2_candidate_complete
CHECK (
    build_version <> 2
    OR (
        (
            manifest_key IS NULL
            AND manifest_sha256 IS NULL
            AND catalog IS NULL
            AND row_count IS NULL
        )
        OR
        (
            manifest_key IS NOT NULL
            AND manifest_sha256 IS NOT NULL
            AND manifest_sha256 ~ '^[a-f0-9]{64}$'
            AND catalog IS NOT NULL
            AND jsonb_typeof(catalog) = 'array'
            AND jsonb_array_length(catalog) > 0
            AND row_count IS NOT NULL
        )
    )
);


CREATE FUNCTION protect_exact_gold_candidate_pin()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.build_version = 2
       AND OLD.manifest_key IS NOT NULL
       AND (
           NEW.manifest_key,
           NEW.manifest_sha256,
           NEW.catalog,
           NEW.row_count
       ) IS DISTINCT FROM (
           OLD.manifest_key,
           OLD.manifest_sha256,
           OLD.catalog,
           OLD.row_count
       )
    THEN
        RAISE EXCEPTION
            'Pinned Exact Gold candidate is immutable';
    END IF;

    RETURN NEW;
END;
$$;


CREATE TRIGGER immutable_exact_gold_candidate_pin
BEFORE UPDATE
ON dataset_gold_runs
FOR EACH ROW
EXECUTE FUNCTION protect_exact_gold_candidate_pin();