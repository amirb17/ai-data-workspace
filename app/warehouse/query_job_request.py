"""414F1: construct a deterministic BigQuery jobs.insert request, without network IO.

This pure builder consumes *server-derived* expected policy and compiled SQL.
It is NOT an authorization gate; callers must first prove authenticated
ownership, live serving receipts, reservation, policy and journal boundary.
No caller may treat its output as a permit to send a BigQuery job.
"""
from dataclasses import dataclass
import hashlib
import re


class JobRequestMismatch(ValueError):
    pass


@dataclass(frozen=True)
class BigQueryJobRequest:
    project: str
    location: str
    job_id: str
    body: dict


def build_reviewed_query_job_request(*, expected: dict, compiled_sql: str) -> BigQueryJobRequest:
    """Canonical bounded QUERY request for a previously attested server policy.

    Request construction is separate from a future send-once transport and
    never performs cloud IO, dry runs, or row/result retrieval.
    """
    required = {
        "contract_version", "project", "location", "dataset", "job_id",
        "sql_sha256", "compiled_intent_sha256", "tables", "labels",
        "maximum_bytes_billed", "job_timeout_ms", "publication_sha256",
        "governance_policy_sha256", "provider_policy", "lookup_policy",
    }
    if type(expected) is not dict or set(expected) != required:
        raise JobRequestMismatch("EXACT_SERVER_POLICY_REQUIRED")
    project, location, dataset = (expected[k] for k in ("project", "location", "dataset"))
    job_id, labels, sql_hash = (expected[k] for k in ("job_id", "labels", "sql_sha256"))
    if (expected["contract_version"] != 1 or type(expected["contract_version"]) is not int
            or type(project) is not str or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project)
            or location != "asia-south1"
            or type(dataset) is not str or not re.fullmatch(r"dr_dev_w[1-9][0-9]*", dataset)
            or type(job_id) is not str or not re.fullmatch(r"dr_query_w[1-9][0-9]*_r[1-9][0-9]*_[a-f0-9]{32}", job_id)):
        raise JobRequestMismatch("INVALID_PROVIDER_SCOPE")
    workspace = dataset.removeprefix("dr_dev_w")
    if not job_id.startswith(f"dr_query_w{workspace}_"):
        raise JobRequestMismatch("WORKSPACE_SCOPE_MISMATCH")
    if (type(compiled_sql) is not str or not 1 <= len(compiled_sql.encode("utf-8")) <= 256 * 1024
            or type(sql_hash) is not str
            or hashlib.sha256(compiled_sql.encode("utf-8")).hexdigest() != sql_hash):
        raise JobRequestMismatch("COMPILED_SQL_FINGERPRINT_MISMATCH")
    tables = expected["tables"]
    if (type(tables) is not list or len(tables) != 1
            or type(tables[0]) is not str
            or not tables[0].startswith(f"{project}.{dataset}.")
            or not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", tables[0].split(".")[-1])
            or tables[0].count(".") != 2):
        raise JobRequestMismatch("PHYSICAL_TABLE_SCOPE_MISMATCH")
    if (type(labels) is not dict or set(labels) != {"datarise", "workspace", "reservation", "intent"}
            or labels.get("datarise") != "query" or labels.get("workspace") != workspace
            or type(labels.get("reservation")) is not str
            or not re.fullmatch(r"[1-9][0-9]*", labels["reservation"])
            or not job_id.startswith(f"dr_query_w{workspace}_r{labels['reservation']}_")
            or type(expected["compiled_intent_sha256"]) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", expected["compiled_intent_sha256"])
            or labels.get("intent") != expected["compiled_intent_sha256"][:63]):
        raise JobRequestMismatch("QUERY_LABELS_MISMATCH")
    maximum, timeout = expected["maximum_bytes_billed"], expected["job_timeout_ms"]
    if (type(maximum) is not int or not 1 <= maximum <= 1024**3
            or type(timeout) is not int or not 1 <= timeout <= 300000):
        raise JobRequestMismatch("UNBOUNDED_QUERY_POLICY")
    provider = expected["provider_policy"]
    lookup = expected["lookup_policy"]
    if (type(provider) is not dict or type(lookup) is not dict
            or provider.get("project") != project or provider.get("location") != location
            or lookup.get("project") != project or lookup.get("location") != location
            or provider.get("maximum_bytes") is None or type(provider["maximum_bytes"]) is not int
            or maximum > provider["maximum_bytes"]
            or type(provider.get("timeout_ms")) is not int
            or timeout != provider["timeout_ms"]
            or type(lookup.get("maximum_timeout_ms")) is not int
            or timeout > lookup["maximum_timeout_ms"]):
        raise JobRequestMismatch("SERVER_PROVIDER_POLICY_DRIFT")
    for digest_name in ("publication_sha256", "governance_policy_sha256"):
        value = expected[digest_name]
        if type(value) is not str or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise JobRequestMismatch("MISSING_PROVENANCE")
    body = {
        "jobReference": {"projectId": project, "location": location, "jobId": job_id},
        "configuration": {
            "jobType": "QUERY",
            "dryRun": False,
            "jobTimeoutMs": str(timeout),
            "labels": dict(labels),
            "query": {
                "query": compiled_sql,
                "useLegacySql": False,
                "useQueryCache": False,
                "priority": "INTERACTIVE",
                "allowLargeResults": False,
                "maximumBytesBilled": str(maximum),
            },
        },
    }
    return BigQueryJobRequest(project, location, job_id, body)
