-- Immutable, source-state-scoped Exact Gold AVG policy.
-- No automatic approval or publication.

CREATE TABLE exact_gold_avg_policies (
    approval_id BIGSERIAL PRIMARY KEY,

    source_state_id BIGINT NOT NULL UNIQUE,
    source_application_id BIGINT NOT NULL,
    dataset_id BIGINT NOT NULL,
    dataset_version_id BIGINT NOT NULL,
    source_policy_id BIGINT NOT NULL,
    applied_rule_version INTEGER NOT NULL,

    policy_version INTEGER NOT NULL DEFAULT 1
        CHECK (policy_version = 1),

    avg_scales JSONB NOT NULL
        CHECK (jsonb_typeof(avg_scales) = 'object'),

    approved_rules_sha256 VARCHAR(64) NOT NULL
        CHECK (approved_rules_sha256 ~ '^[a-f0-9]{64}$'),

    approved_by BIGINT NOT NULL REFERENCES users(user_id),
    approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    FOREIGN KEY (dataset_id, source_state_id)
        REFERENCES dataset_state_versions(dataset_id, state_id),

    FOREIGN KEY (source_state_id, source_application_id)
        REFERENCES dataset_state_versions(state_id, source_application_id),

    FOREIGN KEY (source_policy_id, dataset_id, dataset_version_id)
        REFERENCES dataset_load_policies(
            policy_id, dataset_id, dataset_version_id
        ),

    FOREIGN KEY (dataset_version_id, applied_rule_version)
        REFERENCES approved_rule_policies(
            dataset_version_id, rule_version
        )
);


CREATE FUNCTION guard_exact_gold_avg_policy()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Exact Gold AVG approval is immutable';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM dataset_state_versions s
        JOIN delivery_applications a
          ON a.application_id = s.source_application_id
        JOIN datasets d
          ON d.dataset_id = s.dataset_id
        JOIN users u
          ON u.user_id = NEW.approved_by
         AND u.owner_key = d.owner
        WHERE s.state_id = NEW.source_state_id
          AND s.status = 'PUBLISHED'
          AND s.dataset_id = NEW.dataset_id
          AND s.dataset_version_id = NEW.dataset_version_id
          AND s.policy_id = NEW.source_policy_id
          AND s.source_application_id = NEW.source_application_id
          AND a.applied_rule_version = NEW.applied_rule_version
    ) THEN
        RAISE EXCEPTION
            'Exact Gold AVG approval source pins differ';
    END IF;

    RETURN NEW;
END;
$$;


CREATE TRIGGER immutable_exact_gold_avg_policy
BEFORE INSERT OR UPDATE OR DELETE
ON exact_gold_avg_policies
FOR EACH ROW
EXECUTE FUNCTION guard_exact_gold_avg_policy();