-- 414E2 independent lookup metadata, not a permit or accounting receipt.
BEGIN;
CREATE TABLE warehouse_query_recovery_attestations (
 attempt_id BIGINT NOT NULL,
 journal_revision INTEGER NOT NULL,
 contract_version INTEGER NOT NULL DEFAULT 1 CHECK(contract_version=1),
 observation TEXT NOT NULL CHECK(observation IN ('NOT_FOUND','UNCERTAIN','IN_PROGRESS','SUCCEEDED','FAILED','CANCELLED')),
 verdict TEXT NOT NULL CHECK(verdict IN ('VERIFIED_METADATA','ABSENT','UNCERTAIN','REJECTED')),
 reason TEXT NOT NULL CHECK(reason ~ '^[A-Z_]{1,64}$'),
 policy_sha256 CHAR(64) NOT NULL CHECK(policy_sha256 ~ '^[a-f0-9]{64}$'),
 payload_sha256 CHAR(64) CHECK(payload_sha256 ~ '^[a-f0-9]{64}$'),
 bytes_processed BIGINT CHECK(bytes_processed>=0),
 bytes_billed BIGINT CHECK(bytes_billed>=0),
 billing_complete BOOLEAN NOT NULL,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(attempt_id,journal_revision),
 FOREIGN KEY(attempt_id,journal_revision) REFERENCES warehouse_query_send_journal(attempt_id,revision),
 CHECK(billing_complete=(bytes_processed IS NOT NULL AND bytes_billed IS NOT NULL)),
 CHECK((verdict='VERIFIED_METADATA' AND observation IN ('IN_PROGRESS','SUCCEEDED','FAILED','CANCELLED') AND payload_sha256 IS NOT NULL)
    OR (verdict='ABSENT' AND observation='NOT_FOUND')
    OR (verdict IN ('UNCERTAIN','REJECTED') AND observation='UNCERTAIN'))
);
CREATE FUNCTION warehouse_query_recovery_attestation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Recovery attestation is immutable'; END IF;
 IF NOT EXISTS(SELECT 1 FROM warehouse_query_send_journal j WHERE j.attempt_id=NEW.attempt_id
    AND j.revision=NEW.journal_revision AND j.event='OBSERVATION' AND j.observation=NEW.observation)
 THEN RAISE EXCEPTION 'Matching fenced journal observation required'; END IF;
 NEW.recorded_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER warehouse_recovery_attestation_guard BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_recovery_attestations
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_recovery_attestation_guard();
CREATE TRIGGER warehouse_recovery_attestation_no_truncate BEFORE TRUNCATE ON warehouse_query_recovery_attestations
 EXECUTE FUNCTION query_governance_no_truncate();
REVOKE ALL ON warehouse_query_recovery_attestations FROM PUBLIC;
REVOKE ALL ON FUNCTION warehouse_query_recovery_attestation_guard() FROM PUBLIC;
DO $$ BEGIN
 EXECUTE format('ALTER FUNCTION warehouse_query_recovery_attestation_guard() SET search_path = pg_catalog, %I, pg_temp',current_schema());
END $$;
COMMIT;
