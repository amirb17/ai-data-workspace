"""414F0-B5: static SQL locking dependency inventory, review-only.

This scanner deliberately collects only literal SQL at selected warehouse
boundaries. Its output is not a grant specification or deployment permit.
Dynamic SQL, functions, triggers and transitive repository calls are blockers.
"""
import ast
from dataclasses import dataclass
from pathlib import Path
import re

# Fixed, reviewed repository locations; no caller-supplied paths or SQL.
SOURCES = (
    "app/db/warehouse_query_send_repository.py",
    "app/db/warehouse_query_attempt_repository.py",
    "app/services/warehouse_sql_dispatch_checkpoint_service.py",
    "app/services/warehouse_sql_send_recovery_service.py",
    "app/services/warehouse_sql_recovery_attestation_service.py",
    "app/services/warehouse_sql_provider_policy_service.py",
    "app/services/warehouse_publication_cleanup_inspection_service.py",
)

# A detected lock is a REVIEW ITEM, not proof of complete SQL analysis.
LOCK = re.compile(r"\bFOR\s+(UPDATE|SHARE|NO\s+KEY\s+UPDATE|KEY\s+SHARE)\b", re.I)


@dataclass(frozen=True)
class SqlLockFinding:
    file: str
    line: int
    mode: str
    review_status: str = "REQUIRES_EFFECTIVE_PRIVILEGE_REVIEW"


def inspect_sql_locks(repo_root: Path) -> tuple[SqlLockFinding, ...]:
    findings = []
    for relative in SOURCES:
        tree = ast.parse((repo_root / relative).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            # Collect a literal only when it has SQL-like structure. This is
            # intentionally conservative and cannot prove the absence of locks.
            if not re.search(r"\b(SELECT|UPDATE|DELETE|INSERT)\b", node.value, re.I):
                continue
            for match in LOCK.finditer(node.value):
                findings.append(SqlLockFinding(relative, node.lineno, " ".join(match.group(1).upper().split())))
    return tuple(sorted(findings, key=lambda x: (x.file, x.line, x.mode)))


def lock_review_status(repo_root: Path) -> dict:
    findings = inspect_sql_locks(repo_root)
    return {
        "verdict": "NO_GO",
        "review_complete": False,
        "lock_count": len(findings),
        "findings": findings,
        "blockers": (
            "MISSING_TRANSITIVE_SQL_DEPENDENCY_CLOSURE",
            "TRIGGER_AND_FUNCTION_PRIVILEGES_NOT_VALIDATED",
            "REAL_ROLE_AUTHENTICATION_NOT_VALIDATED",
        ),
    }
