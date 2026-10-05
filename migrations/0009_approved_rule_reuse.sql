-- Distinguish approved policy from active rows created by draft answer saves.
ALTER TABLE dataset_versions ADD COLUMN IF NOT EXISTS approved_rule_version INTEGER;
ALTER TABLE dataset_version_files ADD COLUMN IF NOT EXISTS applied_rule_version INTEGER;
ALTER TABLE dataset_version_files ADD COLUMN IF NOT EXISTS rules_reused BOOLEAN NOT NULL DEFAULT FALSE;

-- Conservative compatibility for pre-marker approvals: require progressed
-- context, active rules, and no pending association that may have changed drafts.
UPDATE dataset_versions dv SET approved_rule_version = rule_version
WHERE approved_rule_version IS NULL AND rule_version > 0
  AND EXISTS (SELECT 1 FROM business_rules br WHERE br.dataset_version_id = dv.dataset_version_id AND br.is_active)
  AND EXISTS (SELECT 1 FROM dataset_version_files f WHERE f.dataset_version_id = dv.dataset_version_id
              AND f.status IN ('READY_FOR_SILVER','SILVER_PROCESSING','SILVER_FAILED','READY_FOR_GOLD','GOLD_PROCESSING','GOLD_FAILED','SUCCESS'))
  AND NOT EXISTS (SELECT 1 FROM dataset_version_files f WHERE f.dataset_version_id = dv.dataset_version_id AND f.status = 'AWAITING_RULES');

UPDATE dataset_version_files f SET applied_rule_version = dv.approved_rule_version
FROM dataset_versions dv WHERE dv.dataset_version_id = f.dataset_version_id
  AND f.applied_rule_version IS NULL AND dv.approved_rule_version IS NOT NULL
  AND f.status IN ('READY_FOR_SILVER','SILVER_PROCESSING','SILVER_FAILED','READY_FOR_GOLD','GOLD_PROCESSING','GOLD_FAILED','SUCCESS');
