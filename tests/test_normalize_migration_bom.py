"""Regression tests: BOM repair never changes actual migration SQL bytes."""
from pathlib import Path

from tools.normalize_migration_bom import BOM, find_bom_files, main, strip_bom


def test_exact_prefix_removed_and_repeated_calls_idempotent(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    with_bom = migrations / "001_test.sql"
    plain = migrations / "002_plain.sql"
    original = b"-- harmless\nBEGIN; SELECT 1; COMMIT;\n"
    with_bom.write_bytes(BOM + original)
    plain.write_bytes(original)

    assert find_bom_files(migrations) == [with_bom]
    assert main(["--root", str(migrations), "--check"]) == 1
    assert main(["--root", str(migrations), "--apply"]) == 0
    assert with_bom.read_bytes() == original
    assert plain.read_bytes() == original
    assert find_bom_files(migrations) == []
    assert strip_bom(with_bom) is False
    assert main(["--root", str(migrations), "--check"]) == 0


def test_internal_bom_and_other_bytes_untouched(tmp_path):
    sql = tmp_path / "migration.sql"
    raw = b"SELECT 1;\n" + BOM + b"SELECT 2;\n"
    sql.write_bytes(raw)
    assert strip_bom(sql) is False
    assert sql.read_bytes() == raw


def test_missing_migration_directory_rejected(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        find_bom_files(tmp_path / "missing")
