BEGIN;

ALTER TABLE business_rule_answers
    DROP CONSTRAINT chk_business_rule_answer;

ALTER TABLE business_rule_answers
    ADD CONSTRAINT chk_business_rule_answer
    CHECK (
        answer IN (
            'YES', 'NO', 'NOT_SURE',
            'ALLOW', 'KEEP_FIRST',
            'KEEP_LATEST', 'QUARANTINE',
            'DECIMAL', 'DATETIME', 'DATE'
        )

        OR CASE
            WHEN answer ~
                '^DECIMAL_EXACT:P([1-9]|[1-6][0-9]|7[0-6]):S(0|[1-9]|[12][0-9]|3[0-8])$'
            THEN
                substring(
                    answer FROM 'P([0-9]+):'
                )::integer
                >=
                substring(
                    answer FROM ':S([0-9]+)$'
                )::integer
            ELSE FALSE
        END
    );

COMMIT;