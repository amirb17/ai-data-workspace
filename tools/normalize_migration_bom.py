"""Normalize UTF-8 BOM in SQL migrations, without changing SQL content.

Usage:
    python -m tools.normalize_migration_bom --check
    python -m tools.normalize_migration_bom --apply

PostgreSQL cannot parse U+FEFF at the beginning of a SQL statement.
The operation only strips the UTF-8 BOM prefix (EF BB BF), never performs
text decoding/re-encoding, and writes atomically in the same directory.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

BOM = b"\xef\xbb\xbf"


def find_bom_files(root: Path) -> list[Path]:
    """Return sorted SQL migration paths with a leading UTF-8 BOM."""
    if not root.is_dir():
        raise FileNotFoundError(f"Migration directory unavailable: {root}")
    paths = sorted(root.glob("*.sql"))
    return [p for p in paths if p.read_bytes().startswith(BOM)]


def strip_bom(path: Path) -> bool:
    """Remove only a leading BOM; preserve bytes and avoid partial writes."""
    data = path.read_bytes()
    if not data.startswith(BOM):
        return False
    descriptor, name = tempfile.mkstemp(prefix=".bom-", suffix=".sql", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(data[len(BOM):])
            target.flush()
            os.fsync(target.fileno())
        os.chmod(name, path.stat().st_mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check/fix SQL migration UTF-8 BOM")
    parser.add_argument("--root", type=Path, default=Path("migrations"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    affected = find_bom_files(args.root)
    if args.apply:
        for path in affected:
            strip_bom(path)
    for path in affected:
        print(path)
    print(f"{len(affected)} migration file(s) {'fixed' if args.apply else 'with UTF-8 BOM'}")
    return 1 if args.check and affected else 0


if __name__ == "__main__":
    raise SystemExit(main())
