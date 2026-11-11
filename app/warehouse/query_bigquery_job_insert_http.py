"""Single-attempt, fail-closed BigQuery jobs.insert HTTP transport.

This is a provider I/O adapter, NOT authorization. A reviewed caller must
first commit the durable send boundary and independently verify server policy.
No credentials are created or refreshed; a trusted worker injects a *plain*
requests.Session with a short-lived bearer token. This transport must never
be shared with the attester or named-result reader, and is deliberately NOT
wired to get_query_runtime() (which remains HTTP 503).
"""
import re
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
_JOB = re.compile(r"dr_query_w([1-9][0-9]*)_r[1-9][0-9]*_[a-f0-9]{32}")
_BASE = "https://bigquery.googleapis.com/bigquery/v2"
_MAX_BODY_BYTES = 256 * 1024
_MAX_BILLED_BYTES = 256 * 1024 * 1024


class BoundedBigQueryJobInsertTransport:
    """One HTTPS POST, no retries, no redirects, no unbounded response reads.

    Session principal/IAM and provenance MUST be externally attested. Merely
    passing session validation does not establish the sender identity.
    """

    def __init__(self, *, project: str, location: str, session: requests.Session):
        if (type(project) is not str or not _PROJECT.fullmatch(project)
                or location != "asia-south1" or type(session) is not requests.Session):
            raise ValueError("REVIEWED_JOB_INSERT_SESSION_REQUIRED")
        self.project, self.location, self.session = project, location, session
        self._verify_session()

    def _verify_session(self):
        s = self.session
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
                and re.fullmatch(r"Bearer [A-Za-z0-9._~+/-]{16,4096}", authorization)
                is not None
            )
        except Exception:
            valid = False
        if not valid:
            raise ValueError("NONRETRYING_SENDER_SESSION_REQUIRED")

    def insert_job_once(self, *, project: str, location: str, body: dict) -> None:
        if project != self.project or location != self.location:
            raise ValueError("SENDER_PROJECT_LOCATION_MISMATCH")
        if type(body) is not dict or set(body) != {"jobReference", "configuration"}:
            raise ValueError("EXACT_JOB_BODY_REQUIRED")
        ref, conf = body["jobReference"], body["configuration"]
        if type(ref) is not dict or type(conf) is not dict:
            raise ValueError("EXACT_JOB_BODY_REQUIRED")
        job_id = ref.get("jobId")
        match = _JOB.fullmatch(job_id) if type(job_id) is str else None
        if (not match or ref != {
            "projectId": project, "location": location, "jobId": job_id
        } or set(conf) != {"jobType", "dryRun", "jobTimeoutMs", "labels", "query"}
                or conf["jobType"] != "QUERY" or conf["dryRun"] is not False):
            raise ValueError("EXACT_JOB_IDENTITY_REQUIRED")
        labels, query = conf["labels"], conf["query"]
        if (type(labels) is not dict or set(labels) !=
                {"datarise", "workspace", "reservation", "intent"}
                or labels.get("datarise") != "query"
                or labels.get("workspace") != match.group(1)
                or type(query) is not dict
                or set(query) != {
                    "query", "useLegacySql", "useQueryCache", "priority",
                    "allowLargeResults", "maximumBytesBilled", "destinationTable",
                    "createDisposition", "writeDisposition",
                }
                or query.get("useLegacySql") is not False
                or query.get("useQueryCache") is not False
                or query.get("priority") != "INTERACTIVE"
                or query.get("allowLargeResults") is not False
                or query.get("createDisposition") != "CREATE_IF_NEEDED"
                or query.get("writeDisposition") != "WRITE_EMPTY"
                or type(query.get("query")) is not str
                or not 1 <= len(query["query"].encode("utf-8")) <= _MAX_BODY_BYTES):
            raise ValueError("REVIEWED_BOUNDED_QUERY_REQUIRED")
        destination = query["destinationTable"]
        if (type(destination) is not dict or destination.get("projectId") != project
                or destination.get("datasetId") != f"dr_results_dev_w{match.group(1)}"
                or type(destination.get("tableId")) is not str
                or not re.fullmatch(r"result_[a-f0-9]{64}", destination["tableId"])
                or set(destination) != {"projectId", "datasetId", "tableId"}
                or type(query["maximumBytesBilled"]) is not str
                or not query["maximumBytesBilled"].isascii()
                or not query["maximumBytesBilled"].isdigit()
                or not 1 <= int(query["maximumBytesBilled"]) <= _MAX_BILLED_BYTES
                or type(conf["jobTimeoutMs"]) is not str
                or not conf["jobTimeoutMs"].isascii()
                or not conf["jobTimeoutMs"].isdigit()
                or not 1 <= int(conf["jobTimeoutMs"]) <= 300000):
            raise ValueError("RESULT_OR_RESOURCE_BOUND_MISMATCH")

        # Verify no session drift before performing any external I/O.
        self._verify_session()
        from json import dumps
        raw = dumps(body, separators=(",", ":"), ensure_ascii=False,
                    allow_nan=False).encode("utf-8")
        if len(raw) > _MAX_BODY_BYTES:
            raise ValueError("JOB_REQUEST_TOO_LARGE")
        url = f"{_BASE}/projects/{project}/jobs"
        response = None
        try:
            response = self.session.request(
                "POST", url, data=raw,
                headers={"Content-Type": "application/json",
                         "Accept": "application/json",
                         "Accept-Encoding": "identity"},
                timeout=(5, 10), stream=True, allow_redirects=False,
            )
            actual = urlparse(response.url)
            expected = urlparse(url)
            if (response.status_code != 200 or actual.scheme != "https"
                    or actual.netloc != expected.netloc
                    or actual.path != expected.path
                    or response.headers.get("content-encoding", "identity").lower()
                    != "identity"):
                raise RuntimeError("JOB_INSERT_OUTCOME_UNKNOWN")
            # Deliberately do not parse/return provider payload: independent
            # jobs.get is required for any acknowledgement, including HTTP 200.
        except Exception:
            raise RuntimeError("JOB_INSERT_OUTCOME_UNKNOWN") from None
        finally:
            if response is not None:
                response.close()
