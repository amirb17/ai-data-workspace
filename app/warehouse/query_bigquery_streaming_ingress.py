"""P0 opt-in bounded HTTP ingress for one BigQuery result GET.

This transport deliberately avoids the BigQuery SDK's eager JSON decoding.
A trusted bootstrap must inject a short-lived, already-authenticated plain
requests.Session. No credentials, principals, IAM or runtime factory are created.
Production query execution remains disabled.

IMPORTANT: The startup review must separately prove the bearer-token principal,
its scope, and the absence of credential-refresh logic. This class cannot grant
BigQuery permissions or attest a cloud identity.
"""
import json
import re
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from app.warehouse.query_bigquery_first_page_adapter import (
    BigQueryResultShapeError, MAX_RAW_RESULT_BYTES,
)

_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
_JOB = re.compile(r"dr_query_w[1-9][0-9]*_r[1-9][0-9]*_[a-f0-9]{32}")
_BASE = "https://bigquery.googleapis.com/bigquery/v2"
_CHUNK = 16 * 1024


class BoundedStreamingBigQueryResultsConnection:
    """Single HTTP GET, streaming into at most 256 KiB before JSON decoding.

    Only a plain requests.Session with an existing bearer authorization and a
    standard non-retrying HTTPS adapter is supported. Automatic OAuth refresh,
    redirects, request-level retries, and raw provider diagnostics are excluded.
    This class is *not* wired into the production dependency.
    """

    def __init__(self, *, project: str, location: str, session: requests.Session):
        if (type(project) is not str or not _PROJECT.fullmatch(project)
                or location != "asia-south1" or type(session) is not requests.Session):
            raise ValueError("REVIEWED_STREAMING_RESULT_SESSION_REQUIRED")
        self.project, self.location, self._session = project, location, session
        self._verify_session()

    def _verify_session(self):
        s = self._session
        try:
            adapter = s.get_adapter(_BASE + "/")
            retry = adapter.max_retries
            authorization = s.headers.get("Authorization")
            valid = (
                type(s) is requests.Session
                and s.trust_env is False
                and s.verify is True
                and s.auth is None
                and not s.cookies
                and type(adapter) is HTTPAdapter
                and retry.total == 0
                and isinstance(authorization, str)
                and re.fullmatch(r"Bearer [A-Za-z0-9._~+/-]{16,4096}", authorization) is not None
                and not s.hooks.get("response")
                and not s.proxies
            )
        except Exception:
            valid = False
        if not valid:
            raise ValueError("NONRETRYING_BEARER_HTTPS_SESSION_REQUIRED")

    def api_request(self, *, method, path, query_params, timeout, retry):
        prefix = f"/projects/{self.project}/queries/"
        if (method != "GET" or type(path) is not str or not path.startswith(prefix)
                or not _JOB.fullmatch(path[len(prefix):])
                or type(query_params) is not dict
                or set(query_params) != {"location", "maxResults", "timeoutMs"}
                or query_params["location"] != self.location
                or type(query_params["maxResults"]) is not int
                or not 1 <= query_params["maxResults"] <= 100
                or type(query_params["timeoutMs"]) is not int
                or query_params["timeoutMs"] != 1000
                or timeout != 10 or type(timeout) is not int or retry is not None):
            raise ValueError("EXACT_STREAMING_RESULT_GET_REQUIRED")
        self._verify_session()
        url = _BASE + path
        response = None
        try:
            response = self._session.request(
                "GET", url, params=dict(query_params),
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                timeout=(5, 10), stream=True, allow_redirects=False,
            )
            if (response.status_code != 200
                    or urlparse(response.url).scheme != "https"
                    or urlparse(response.url).netloc != "bigquery.googleapis.com"
                    or urlparse(response.url).path != urlparse(url).path
                    or response.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json"
                    or response.headers.get("content-encoding", "identity").lower() != "identity"):
                raise BigQueryResultShapeError("STREAMING_RESULT_HTTP_REJECTED")
            length = response.headers.get("content-length")
            if length is not None:
                if not length.isascii() or not length.isdigit() or int(length) > MAX_RAW_RESULT_BYTES:
                    raise BigQueryResultShapeError("PROVIDER_RESPONSE_TOO_LARGE")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=_CHUNK):
                if type(chunk) is not bytes:
                    raise BigQueryResultShapeError("INVALID_STREAMING_RESULT_CHUNK")
                if len(body) + len(chunk) > MAX_RAW_RESULT_BYTES:
                    raise BigQueryResultShapeError("PROVIDER_RESPONSE_TOO_LARGE")
                body.extend(chunk)
            try:
                result = json.loads(body)
            except (ValueError, UnicodeError, RecursionError, TypeError):
                raise BigQueryResultShapeError("INVALID_PROVIDER_RESPONSE") from None
            if type(result) is not dict:
                raise BigQueryResultShapeError("INVALID_PROVIDER_RESPONSE")
            return result
        except BigQueryResultShapeError:
            raise
        except Exception:
            raise BigQueryResultShapeError("RESULT_PROVIDER_READ_UNAVAILABLE") from None
        finally:
            if response is not None:
                response.close()
