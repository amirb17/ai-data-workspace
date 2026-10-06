-- Execution must use the approved policy pinned by the delivery, not mutable drafts.
CREATE TABLE approved_rule_policies (
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_versions(dataset_version_id),
    rule_version INTEGER NOT NULL CHECK (rule_version > 0),
    rules JSONB NOT NULL CHECK (jsonb_typeof(rules) = 'array'),
    approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (dataset_version_id, rule_version)
);

-- Only the currently approved version can be reconstructed safely from legacy rows.
INSERT INTO approved_rule_policies(dataset_version_id, rule_version, rules)
SELECT dv.dataset_version_id, dv.rule_version,
       jsonb_agg(jsonb_build_object('column_name', br.column_name,
                 'rule_type', br.rule_type, 'rule_config', br.rule_config) ORDER BY br.rule_id)
FROM dataset_versions dv JOIN business_rules br ON br.dataset_version_id = dv.dataset_version_id
WHERE dv.approved_rule_version = dv.rule_version AND dv.rule_version > 0 AND br.is_active
GROUP BY dv.dataset_version_id, dv.rule_version;
