"""Bounded read-only BigQuery named-result table transport (C8).

The independently authorized result-reader principal reads exactly the pinned
destination TABLE using tabledata.list, not creator-only getQueryResults.
Existing IndependentNamedResultReader performs metadata proof checks before
and after this read; this adapter itself is not a result authorization gate.
No ADC, credential factory, job submission, pagination or runtime enablement.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from app.warehouse.named_query_results import validate_result_contract
from app.warehouse.query_bigquery_first_page_adapter import (
    BigQueryResultShapeError, MAX_RAW_RESULT_BYTES,
)

_BASE = "https://bigquery.googleapis.com/bigquery/v2"
_CHUNK = 16 * 1024
_EMAIL = re.compile(r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com\Z")
_TOKEN = re.compile(r"Bearer [A-Za-z0-9._~+/-]{16,4096}\Z")
_KIND = {
    "STRING": "STRING",
    "INTEGER": "INTEGER",
    "NUMERIC": "NUMERIC",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
}


class NamedTableBigQueryResultReader:
    """One GET for a pinned table using a distinct, externally attested reader.

    The supplied plain requests.Session must already contain the short-lived
    reader bearer token. The caller owns the session; this class never refreshes
    tokens or follows redirects.
    """

    def __init__(
        self, *, expected: dict, principal: str, session: requests.Session
    ):
        if (type(expected) is not dict or expected.get("contract_version") != 2
                or type(principal) is not str or not _EMAIL.fullmatch(principal)
                or type(session) is not requests.Session):
            raise ValueError("REVIEWED_NAMED_RESULT_READER_REQUIRED")
        project = expected.get("project")
        source = expected.get("dataset")
        job = expected.get("job_id")
        contract = validate_result_contract(
            expected.get("result"), project, source, job
        )
        policy = contract["policy"]
        if (principal != policy["reader_principal"]
                or principal == expected.get("provider_policy", {}).get("creator_email")
                or expected.get("location") != "asia-south1"):
            raise ValueError("INDEPENDENT_NAMED_READER_PRINCIPAL_REQUIRED")
        self.expected = expected
        self.principal = principal
        self._session = session
        self._destination = contract["destination"]
        self._schema = tuple(tuple(c) for c in contract["schema"])
        self._verify_session()

    def _verify_session(self):
        s = self._session
        try:
            adapter = s.get_adapter(_BASE + "/")
            authorization = s.headers.get("Authorization")
            valid = (
                type(s) is requests.Session
                and s.trust_env is False and s.verify is True
                and s.auth is None and not s.cookies and not s.proxies
                and not s.hooks.get("response")
                and type(adapter) is HTTPAdapter
                and adapter.max_retries.total == 0
                and isinstance(authorization, str)
                and _TOKEN.fullmatch(authorization) is not None
            )
        except Exception:
            valid = False
        if not valid:
            raise ValueError("NONRETRYING_READER_SESSION_REQUIRED")

    def get_query_results_once(
        self, *, project: str, location: str, job_id: str, max_results: int
    ) -> dict:
        expected = self.expected
        result = expected["result"]
        if (project != expected["project"]
                or location != expected["location"]
                or job_id != expected["job_id"]
                or type(max_results) is not int
                or not 1 <= max_results <= min(100, result["maximum_rows"])
                or int(time.time() * 1000) >= result["read_until_ms"]):
            raise ValueError("EXACT_UNEXPIRED_NAMED_TABLE_SCOPE_REQUIRED")
        self._verify_session()
        dest = self._destination
        url = (
            f"{_BASE}/projects/{dest['projectId']}/datasets/"
            f"{dest['datasetId']}/tables/{dest['tableId']}/data"
        )
        response = None
        try:
            response = self._session.request(
                "GET", url,
                params={"maxResults": max_results, "startIndex": "0"},
                headers={"Accept": "application/json",
                         "Accept-Encoding": "identity"},
                timeout=(5, 10), stream=True, allow_redirects=False,
            )
            parsed = urlparse(response.url)
            target = urlparse(url)
            if (response.status_code != 200
                    or parsed.scheme != "https"
                    or parsed.netloc != "bigquery.googleapis.com"
                    or parsed.path != target.path
                    or response.headers.get("content-type", "").split(";")[0].strip().lower()
                    != "application/json"
                    or response.headers.get("content-encoding", "identity").lower()
                    != "identity"):
                raise BigQueryResultShapeError("NAMED_TABLE_HTTP_REJECTED")
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isascii() or not length.isdigit()
                or int(length) > MAX_RAW_RESULT_BYTES
            ):
                raise BigQueryResultShapeError("PROVIDER_RESPONSE_TOO_LARGE")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=_CHUNK):
                if type(chunk) is not bytes:
                    raise BigQueryResultShapeError("INVALID_RESULT_CHUNK")
                if len(body) + len(chunk) > MAX_RAW_RESULT_BYTES:
                    raise BigQueryResultShapeError("PROVIDER_RESPONSE_TOO_LARGE")
                body.extend(chunk)
            raw = json.loads(body)
            if type(raw) is not dict or set(raw) - {
                "kind", "etag", "totalRows", "pageToken", "rows"
            }:
                raise BigQueryResultShapeError("INVALID_NAMED_TABLE_RESPONSE")
            if "kind" in raw and raw["kind"] != "bigquery#tableDataList":
                raise BigQueryResultShapeError("INVALID_NAMED_TABLE_RESPONSE")
            rows = raw.get("rows", [])
            if type(rows) is not list or len(rows) > max_results:
                raise BigQueryResultShapeError("NAMED_TABLE_ROW_LIMIT_EXCEEDED")
            if "pageToken" in raw and (
                type(raw["pageToken"]) is not str
                or not 1 <= len(raw["pageToken"]) <= 4096
            ):
                raise BigQueryResultShapeError("INVALID_NAMED_TABLE_PAGE_TOKEN")
            converted = {
                "jobReference": {
                    "projectId": project, "location": location, "jobId": job_id
                },
                "jobComplete": True,
                "schema": {"fields": [
                    {"name": name, "type": _KIND[kind], "mode": "NULLABLE"}
                    for name, kind in self._schema
                ]},
                "rows": rows,
            }
            if "pageToken" in raw:
                converted["pageToken"] = raw["pageToken"]
            if "totalRows" in raw:
                converted["totalRows"] = raw["totalRows"]
            # An extra validation layer applies the original pinned schema and
            # exact row/cell types at the existing higher-level bridge.
            return converted
        except BigQueryResultShapeError:
            raise
        except Exception:
            raise BigQueryResultShapeError("NAMED_TABLE_READ_UNAVAILABLE") from None
        finally:
            if response is not None:
                response.close()


class PinnedNamedTableResultReaderFactory:
    """Inject a reviewed reader session once; bind exact job/destination later.

    Transport-only, with no database login. The bearer token must belong to
    the dedicated result reader, not an API/sender/attester process credential.
    """

    def __init__(self, *, principal: str, session: requests.Session):
        if (type(principal) is not str or not _EMAIL.fullmatch(principal)
                or type(session) is not requests.Session):
            raise ValueError("EXPLICIT_RESULT_READER_FACTORY_REQUIRED")
        self.principal = principal
        self._session = session

    def bind(self, expected: dict) -> NamedTableBigQueryResultReader:
        return NamedTableBigQueryResultReader(
            expected=expected, principal=self.principal, session=self._session
        )
