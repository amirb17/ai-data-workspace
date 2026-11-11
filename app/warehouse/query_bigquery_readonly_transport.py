"""414F5 narrowly scoped read-only jobs.getQueryResults REST boundary.

Requires an injected, explicitly reviewed BigQuery JSON connection. Never
creates credentials, clients, jobs, SQL, cursors or any cloud runtime itself.
Not bound to any production entrypoint. Backend authorization and server
schema/pin checks must run before it can be wired to live IAM.
"""
from dataclasses import dataclass
import re
from typing import Protocol

from app.warehouse.query_bigquery_first_page_adapter import (
    BigQueryResultShapeError, validate_bigquery_first_page,
)

_JOB=re.compile(r"dr_query_w[1-9][0-9]*_r[1-9][0-9]*_[a-f0-9]{32}")
_PROJECT=re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")


class BigQueryJsonConnection(Protocol):
    def api_request(
        self, *, method: str, path: str, query_params: dict,
        timeout: int, retry: None,
    ) -> object: ...


@dataclass(frozen=True)
class ReviewedResultsConnection:
    """Explicit startup-supplied project/location-scoped connection."""
    project: str
    location: str
    connection: BigQueryJsonConnection

    def __post_init__(self):
        if (type(self.project) is not str or not _PROJECT.fullmatch(self.project)
                or self.location!="asia-south1"
                or not callable(getattr(self.connection,"api_request",None))):
            raise ValueError("REVIEWED_BQ_READ_CONNECTION_REQUIRED")


class ReadOnlyBigQueryFirstPage:
    """At most one GET to an exact original query job; no retry/reissue."""
    def __init__(self, reviewed: ReviewedResultsConnection):
        if type(reviewed) is not ReviewedResultsConnection:
            raise TypeError("Explicit reviewed connection required")
        self._reviewed=reviewed

    def get_query_results_once(
        self, *, project: str, location: str,
        job_id: str, max_results: int,
    ) -> dict:
        if (type(project) is not str or project!=self._reviewed.project
                or location!=self._reviewed.location
                or type(job_id) is not str or not _JOB.fullmatch(job_id)
                or type(max_results) is not int or not 1<=max_results<=100):
            raise ValueError("ORIGINAL_BOUNDED_RESULT_SCOPE_REQUIRED")
        try:
            raw=self._reviewed.connection.api_request(
                method="GET",
                path=f"/projects/{project}/queries/{job_id}",
                query_params={
                    "location":location,
                    "maxResults":max_results,
                    "timeoutMs":1000,
                },
                timeout=10,
                retry=None,
            )
        except Exception:
            # No SDK error strings, credentials, or job internals leak.
            raise BigQueryResultShapeError("RESULT_PROVIDER_READ_UNAVAILABLE") from None
        if type(raw) is not dict:
            raise BigQueryResultShapeError("INVALID_PROVIDER_RESPONSE")
        return raw
