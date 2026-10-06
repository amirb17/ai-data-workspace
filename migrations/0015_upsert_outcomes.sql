-- Additive UPSERT metrics; immutable application/state history guards already apply.
ALTER TABLE delivery_applications ADD COLUMN conflict_rows BIGINT CHECK(conflict_rows>=0);
ALTER TABLE delivery_applications ADD COLUMN stale_rows BIGINT CHECK(stale_rows>=0);
