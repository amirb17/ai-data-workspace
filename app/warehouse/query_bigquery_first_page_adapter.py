"""414F5 BigQuery jobs.getQueryResults first-page adapter (explicit fake transport).

This module does not construct a cloud client, accept credentials, issue
jobs.insert or expose provider pagination tokens to HTTP. A separately
reviewed production binding must enforce IAM, timeout and retry=None.
"""
import json
import math
import re
from typing import Protocol

from app.warehouse.query_result_pages import validate_result_page, ResultPageConflict

MAX_RAW_RESULT_BYTES=256*1024
BQ_TO_APPROVED={
    "STRING":"STRING", "INTEGER":"INTEGER", "INT64":"INTEGER",
    "FLOAT":"FLOAT", "FLOAT64":"FLOAT", "BOOLEAN":"BOOLEAN", "BOOL":"BOOLEAN",
    "NUMERIC":"NUMERIC", "DATE":"DATE", "TIMESTAMP":"TIMESTAMP",
}

class BigQueryResultTransport(Protocol):
    def get_query_results_once(self, *, project: str, location: str,
                               job_id: str, max_results: int) -> dict: ...


class BigQueryResultShapeError(ResultPageConflict):
    pass


def _convert(value, kind):
    if value is None:
        return None
    if type(value) is not str:
        raise BigQueryResultShapeError("INVALID_PROVIDER_SCALAR")
    try:
        if kind=="INTEGER":
            if re.fullmatch(r"-?(?:0|[1-9][0-9]*)",value) is None:
                raise ValueError()
            number=int(value)
            if not -(2**63)<=number<2**63:
                raise ValueError()
            return number
        if kind=="BOOLEAN":
            if value not in ("true","false"):
                raise ValueError()
            return value=="true"
        if kind=="FLOAT":
            number=float(value)
            if not math.isfinite(number):
                raise ValueError()
            return number
        if kind=="NUMERIC":
            if len(value)>100 or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?",value):
                raise ValueError()
        if kind in ("STRING","DATE","TIMESTAMP","NUMERIC"):
            if len(value)>8192:
                raise ValueError()
            return value
    except (ValueError,OverflowError):
        raise BigQueryResultShapeError("INVALID_PROVIDER_SCALAR") from None
    raise BigQueryResultShapeError("UNSUPPORTED_PROVIDER_TYPE")


def validate_bigquery_first_page(
    *, project: str, location: str, job_id: str,
    approved_schema: tuple[tuple[str,str],...],
    requested_rows: int, raw: dict,
):
    """Convert narrowly accepted BigQuery REST first-page shape to safe page."""
    if (type(project) is not str
            or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]",project)
            or location!="asia-south1"):
        raise BigQueryResultShapeError("REVIEWED_PROJECT_SCOPE_REQUIRED")
    if type(raw) is not dict:
        raise BigQueryResultShapeError("INVALID_PROVIDER_RESPONSE")
    # The provider may return more rows than the requested page. Reject before
    # decoding any cells or serializing the full response a second time.
    raw_rows=raw.get("rows",[])
    if type(raw_rows) is not list:
        raise BigQueryResultShapeError("INVALID_RESULT_ROWS")
    if (type(requested_rows) is not int or not 1<=requested_rows<=100
            or len(raw_rows)>requested_rows):
        raise BigQueryResultShapeError("ROW_LIMIT_EXCEEDED")
    # JSONEncoder.iterencode allows us to stop measuring at 256 KiB without
    # constructing a second unbounded JSON document via json.dumps().
    encoded_bytes=0
    try:
        for chunk in json.JSONEncoder(
            separators=(",",":"),ensure_ascii=False,allow_nan=False
        ).iterencode(raw):
            encoded_bytes+=len(chunk.encode("utf-8"))
            if encoded_bytes>MAX_RAW_RESULT_BYTES:
                raise BigQueryResultShapeError("PROVIDER_RESPONSE_TOO_LARGE")
    except BigQueryResultShapeError:
        # Preserve the precise bounded-size rejection. It is a ValueError
        # subclass, so the generic encoding exception handler would mask it.
        raise
    except (TypeError,ValueError,OverflowError,RecursionError):
        raise BigQueryResultShapeError("INVALID_PROVIDER_RESPONSE") from None
    if not raw.get("jobComplete") is True or raw.get("errors"):
        raise BigQueryResultShapeError("INCOMPLETE_OR_FAILED_JOB")
    if set(raw)-{"kind","etag","jobReference","jobComplete","schema","rows","pageToken","totalRows","cacheHit","totalBytesProcessed"}:
        raise BigQueryResultShapeError("UNREVIEWED_RESULT_FIELDS")
    ref=raw.get("jobReference")
    if ref != {"projectId":project,"location":location,"jobId":job_id}:
        raise BigQueryResultShapeError("RESULT_JOB_SCOPE_MISMATCH")
    schema=raw.get("schema")
    if type(schema) is not dict or set(schema)!={"fields"} or type(schema["fields"]) is not list:
        raise BigQueryResultShapeError("RESULT_SCHEMA_MISSING")
    if type(approved_schema) is not tuple or len(schema["fields"])!=len(approved_schema):
        raise BigQueryResultShapeError("RESULT_SCHEMA_MISMATCH")
    for field,(name,kind) in zip(schema["fields"],approved_schema):
        if (type(field) is not dict or not {"name","type"}<=set(field)
                or set(field)-{"name","type","mode","description","maxLength","precision","scale"}
                or field["name"]!=name
                or BQ_TO_APPROVED.get(field["type"])!=kind
                or field.get("mode","NULLABLE") not in ("NULLABLE","REQUIRED")):
            raise BigQueryResultShapeError("RESULT_SCHEMA_MISMATCH")
    converted=[]
    for row in raw_rows:
        if type(row) is not dict or set(row)!={"f"} or type(row["f"]) is not list or len(row["f"])!=len(approved_schema):
            raise BigQueryResultShapeError("INVALID_RESULT_ROW")
        vals=[]
        for cell,(_,kind) in zip(row["f"],approved_schema):
            if type(cell) is not dict or set(cell)!={"v"}:
                raise BigQueryResultShapeError("INVALID_RESULT_CELL")
            vals.append(_convert(cell["v"],kind))
        converted.append(vals)
    page={
        "job_id":job_id,
        "schema":[{"name":name,"type":kind} for name,kind in approved_schema],
        "rows":converted,
        "next_page_token":raw.get("pageToken"),
    }
    return validate_result_page(
        job_id=job_id,approved_schema=approved_schema,
        response=page,requested_rows=requested_rows,
    )
