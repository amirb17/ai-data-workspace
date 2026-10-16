-- Extend the explicit rule-answer allowlist for calendar dates.
-- Existing values, approved rules, snapshots and business data are unchanged.
ALTER TABLE business_rule_answers DROP CONSTRAINT chk_business_rule_answer;
ALTER TABLE business_rule_answers ADD CONSTRAINT chk_business_rule_answer
    CHECK (answer IN (
        'YES', 'NO', 'NOT_SURE',
        'ALLOW', 'KEEP_FIRST', 'KEEP_LATEST', 'QUARANTINE',
        'DECIMAL', 'DATETIME', 'DATE'
    ));
