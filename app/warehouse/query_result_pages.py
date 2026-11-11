"""414F5: bounded, strict result page validation, no provider IO.

Only trusted workers may supply columns from the approved compiler and a
verified job scope. This pure function is not an authorization gate.
"""
from dataclasses import dataclass
import json
import re

MAX_PAGE_ROWS = 100
MAX_PAGE_BYTES = 256 * 1024
MAX_COLUMNS = 64
_ALLOWED_TYPES = frozenset({"STRING", "INTEGER", "FLOAT", "BOOLEAN", "NUMERIC", "DATE", "TIMESTAMP"})


class ResultPageConflict(ValueError):
    pass


@dataclass(frozen=True)
class ResultPage:
    job_id: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    next_page_token: str | None
    bytes_encoded: int
    truncated: bool = False


def validate_result_page(
    *, job_id: str, approved_schema: tuple[tuple[str, str], ...],
    response: dict, requested_rows: int,
) -> ResultPage:
    """Validate exact schema, bounded values, pagination and response size.

    Validates a server-provided result-page mapping, not arbitrary Python
    objects. Nested/repeated RECORD values, NaN and unknown types fail closed.
    Pagination tokens are opaque and must never be accepted from an API
    caller without an independent trusted cursor binding.
    """
    if type(job_id) is not str or not re.fullmatch(
        r"dr_query_w[1-9][0-9]*_r[1-9][0-9]*_[a-f0-9]{32}", job_id
    ):
        raise ResultPageConflict("EXACT_JOB_REQUIRED")
    if (type(requested_rows) is not int or not 1 <= requested_rows <= MAX_PAGE_ROWS
            or type(approved_schema) is not tuple
            or not 1 <= len(approved_schema) <= MAX_COLUMNS):
        raise ResultPageConflict("SERVER_BOUNDS_REQUIRED")
    names = []
    for column in approved_schema:
        if (type(column) is not tuple or len(column) != 2
                or type(column[0]) is not str
                or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,127}", column[0])
                or column[0].lower() in {n.lower() for n in names}
                or type(column[1]) is not str
                or column[1] not in _ALLOWED_TYPES):
            raise ResultPageConflict("APPROVED_SCHEMA_REQUIRED")
        names.append(column[0])
    if type(response) is not dict or set(response) != {"job_id", "schema", "rows", "next_page_token"}:
        raise ResultPageConflict("EXACT_PAGE_SHAPE_REQUIRED")
    if response["job_id"] != job_id or type(response["schema"]) is not list:
        raise ResultPageConflict("JOB_OR_SCHEMA_MISMATCH")
    actual = response["schema"]
    if actual != [{"name": n, "type": t} for n, t in approved_schema]:
        raise ResultPageConflict("JOB_OR_SCHEMA_MISMATCH")
    rows = response["rows"]
    if type(rows) is not list or len(rows) > requested_rows:
        raise ResultPageConflict("ROW_LIMIT_EXCEEDED")
    safe_rows = []
    for row in rows:
        if type(row) is not list or len(row) != len(approved_schema):
            raise ResultPageConflict("ROW_SHAPE_MISMATCH")
        safe = []
        for value, (_, typ) in zip(row, approved_schema):
            if value is None:
                safe.append(None)
                continue
            if typ in ("STRING", "DATE", "TIMESTAMP", "NUMERIC"):
                valid = type(value) is str and len(value) <= 8192
            elif typ == "INTEGER":
                valid = type(value) is int and -(2**63) <= value < 2**63
            elif typ == "FLOAT":
                valid = type(value) in (int, float)
            else:
                valid = type(value) is bool
            if not valid:
                raise ResultPageConflict("UNSAFE_RESULT_VALUE")
            safe.append(value)
        safe_rows.append(tuple(safe))
    token = response["next_page_token"]
    if token is not None and (type(token) is not str or not 1 <= len(token) <= 2048):
        raise ResultPageConflict("INVALID_PROVIDER_CURSOR")
    try:
        encoded = json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise ResultPageConflict("UNSAFE_RESULT_ENCODING") from None
    if len(encoded) > MAX_PAGE_BYTES:
        raise ResultPageConflict("RESULT_PAYLOAD_LIMIT")
    return ResultPage(job_id, tuple(names), tuple(safe_rows), token, len(encoded))
