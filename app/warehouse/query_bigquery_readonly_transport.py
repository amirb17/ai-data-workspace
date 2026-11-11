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


class BigQuerySdkResultsConnection:
    """Adapt a reviewed SDK Client to the existing GET-only protocol.

    Client._connection.api_request does not accept retry. Client._call_api
    consumes retry=None before forwarding to it. No client/credential factory,
    admission authority, submission, result authorization or IAM grant lives here.
    The caller owns/ closes the client; the production workflow remains disabled.
    """
    def __init__(self, client):
        from google.cloud.bigquery import Client
        if (not isinstance(client, Client)
                or type(client.project) is not str or not _PROJECT.fullmatch(client.project)
                or client.location != 'asia-south1'
                or client._connection.get_api_base_url_for_mtls() != 'https://bigquery.googleapis.com'):
            raise ValueError('REVIEWED_SDK_RESULT_CLIENT_REQUIRED')
        self._client = client
        self._project = client.project
        self._location = client.location

    def api_request(self, *, method, path, query_params, timeout, retry):
        prefix = f'/projects/{self._project}/queries/'
        if (method != 'GET' or type(path) is not str or not path.startswith(prefix)
                or not _JOB.fullmatch(path[len(prefix):])
                or type(query_params) is not dict
                or set(query_params) != {'location', 'maxResults', 'timeoutMs'}
                or query_params['location'] != self._location
                or type(query_params['maxResults']) is not int
                or not 1 <= query_params['maxResults'] <= 100
                or type(query_params['timeoutMs']) is not int or query_params['timeoutMs'] != 1000
                or type(timeout) is not int or timeout != 10 or retry is not None
                or self._client.project != self._project or self._client.location != self._location
                or self._client._connection.get_api_base_url_for_mtls() != 'https://bigquery.googleapis.com'):
            raise ValueError('EXACT_READ_ONLY_SDK_REQUEST_REQUIRED')
        return self._client._call_api(
            retry=None, method='GET', path=path,
            query_params=dict(query_params), timeout=10,
        )
