"""Regression: trusted cumulative Gold publish must not confuse its JSON arg with the table column."""
from pathlib import Path


def test_catalog_assignment_qualified_in_upgrade():
    migration = (Path(__file__).resolve().parents[1] /
                 'migrations/0064_cumulative_gold_publish_catalog_collision.sql').read_text()
    assert 'CREATE OR REPLACE FUNCTION task21_analytics_publish(' in migration
    assert 'catalog=task21_analytics_publish.catalog,row_count=source_rows' in migration
    assert 'catalog=catalog,row_count=source_rows' not in migration
    assert 'SECURITY DEFINER' in migration
    assert 'task21_analytics_owner(actor,w,d)' in migration
    assert 'source_rows' in migration
    assert 'COMMIT;' in migration
