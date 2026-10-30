CREATE FUNCTION protect_successful_silver_checksum()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_TABLE_NAME = 'data_quality_runs' THEN

        IF OLD.silver_sha256 IS NOT NULL
           AND EXISTS (
               SELECT 1
               FROM processing_attempts pa
               WHERE pa.attempt_id = OLD.attempt_id
                 AND pa.stage = 'SILVER'
                 AND pa.status = 'SUCCESS'
           )
        THEN
            RAISE EXCEPTION
                'Successful Silver checksum evidence is immutable';
        END IF;

    ELSIF TG_TABLE_NAME = 'processing_attempts' THEN

        IF OLD.stage = 'SILVER'
           AND OLD.status = 'SUCCESS'
           AND EXISTS (
               SELECT 1
               FROM data_quality_runs dq
               WHERE dq.attempt_id = OLD.attempt_id
                 AND dq.silver_sha256 IS NOT NULL
           )
        THEN
            RAISE EXCEPTION
                'Successful Silver checksum evidence is immutable';
        END IF;

    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER protect_silver_dq_checksum
BEFORE UPDATE OR DELETE ON data_quality_runs
FOR EACH ROW
EXECUTE FUNCTION protect_successful_silver_checksum();

CREATE TRIGGER protect_silver_checksum_attempt
BEFORE UPDATE OR DELETE ON processing_attempts
FOR EACH ROW
EXECUTE FUNCTION protect_successful_silver_checksum();