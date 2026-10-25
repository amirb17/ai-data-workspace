"""Governed BigQuery analytical reads.

Separate from the warehouse mutation Target protocol. A dry run estimates and
verifies sources. Execution requires a durable EXECUTING reservation permit.
"""

from datetime import datetime, timezone
from itertools import islice
import json
import time
from collections.abc import Mapping

from google.cloud import bigquery

from app.query_governance.providers.base import (
    MAX_RESULT_PAYLOAD_BYTES,
    CompiledQuery,
    ExecutionPermit,
    QueryExecutionResult,
    QueryRecovery,
    RecoveryState,
)
from app.query_governance.types import (
    EstimateConfidence,
    QueryEstimate,
)
from app.warehouse.providers.base import (
    ErrorCode,
    Policy,
    ProviderError,
)
from app.warehouse.providers.bigquery import classify
import re


_PUBLICATION_TABLE = re.compile(
    r"^m(?P<model>[1-9][0-9]*)"
    r"_v(?P<version>[1-9][0-9]*)"
    r"_g(?P<generation>[1-9][0-9]*)"
    r"_p(?P<publication>[1-9][0-9]*)"
    r"_r(?P<revision>[1-9][0-9]*)$"
)

BIGQUERY_MINIMUM_BILLED_BYTES_PER_TABLE = 10 * 1024 * 1024

_ACCURACY = {
    "PRECISE": EstimateConfidence.EXACT,
    "UPPER_BOUND": EstimateConfidence.UPPER_BOUND,
    "LOWER_BOUND": EstimateConfidence.LOWER_BOUND,
    "UNKNOWN": EstimateConfidence.UNKNOWN,
}


def _reference_name(reference) -> str | None:
    if isinstance(reference, Mapping):
        project = (
            reference.get("projectId")
            or reference.get("project_id")
            or reference.get("project")
        )
        dataset = (
            reference.get("datasetId")
            or reference.get("dataset_id")
        )
        table = (
            reference.get("tableId")
            or reference.get("table_id")
        )
    else:
        project = getattr(reference, "project", None)
        dataset = getattr(reference, "dataset_id", None)
        table = getattr(reference, "table_id", None)

    if not all(isinstance(value, str) and value for value in (
        project,
        dataset,
        table,
    )):
        return None

    return f"{project}.{dataset}.{table}"


def _query_statistics(job) -> dict:
    properties = getattr(job, "_properties", {}) or {}
    statistics = properties.get("statistics", {}) or {}
    query = statistics.get("query", {}) or {}

    return query if isinstance(query, dict) else {}


def _references(job):
    direct = getattr(job, "referenced_tables", None)

    if direct is None:
        direct = _query_statistics(job).get(
            "referencedTables",
            (),
        )

    names = []

    for reference in direct or ():
        name = _reference_name(reference)

        if name is None:
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)

        names.append(name)

    return tuple(names)


def _referenced_routines(job):
    direct = getattr(job, "referenced_routines", None)

    if direct:
        return direct

    return _query_statistics(job).get(
        "referencedRoutines",
        (),
    ) or ()


def _referenced_views(job):
    direct = getattr(job, "referenced_logical_views", None)

    if direct:
        return direct

    return _query_statistics(job).get(
        "referencedLogicalViews",
        (),
    ) or ()


def _statement_type(job):
    value = getattr(job, "statement_type", None)

    if value:
        return value

    return _query_statistics(job).get("statementType")


def _accuracy(job):
    value = _query_statistics(job).get(
        "totalBytesProcessedAccuracy"
    )

    return _ACCURACY.get(
        value,
        EstimateConfidence.UNKNOWN,
    )


class BigQueryReadProvider:
    identity = "BIGQUERY"

    def __init__(
        self,
        policy: Policy,
        client=None,
    ):
        if not policy.enabled:
            raise ProviderError(ErrorCode.DISABLED)

        self.policy = policy

        try:
            self.client = (
                client
                if client is not None
                else bigquery.Client(
                    project=policy.project,
                    location=policy.location,
                )
            )
        except Exception as error:
            raise classify(error) from None

        if self.client.project != policy.project:
            raise ProviderError(ErrorCode.PERMISSION_DENIED)
    
    def _call(self, function, *args, **kwargs):
        try:
            return function(
                *args,
                retry=None,
                timeout=self.policy.timeout_seconds,
                **kwargs,
            )
        except ProviderError:
            raise
        except Exception as error:
            raise classify(error) from None

    def _verify_query_scope(self, query: CompiledQuery):
        if not isinstance(query, CompiledQuery):
            raise ValueError("Compiled query required")

        prefix = self.policy.project + "."

        if any(
            not table.startswith(prefix)
            for table in query.physical_tables
        ):
            raise ProviderError(ErrorCode.PERMISSION_DENIED)

    def _verify_table(self, name: str, workspace_id: int):
        table = self._call(
            self.client.get_table,
            name,
        )

        full_name = getattr(table, "full_table_id", None)

        if isinstance(full_name, str):
            full_name = full_name.replace(":", ".", 1)
        else:
            project = getattr(table, "project", None)
            dataset = getattr(table, "dataset_id", None)
            table_id = getattr(table, "table_id", None)

            if all(isinstance(v, str) for v in (
                project,
                dataset,
                table_id,
            )):
                full_name = f"{project}.{dataset}.{table_id}"

        labels = getattr(table, "labels", {}) or {}
        table_id = name.rsplit(
            ".",
            1,
        )[-1]

        publication = (
            _PUBLICATION_TABLE.fullmatch(
                table_id
            )
        )

        if publication is not None:
            if (
                labels.get("model")
                != publication.group("model")
                or labels.get("generation")
                != publication.group(
                    "generation"
                )
                or labels.get("publication")
                != publication.group(
                    "publication"
                )
                or labels.get("revision")
                != publication.group(
                    "revision"
                )
            ):
                raise ProviderError(
                    ErrorCode.PERMISSION_DENIED
                )

        if (
            full_name != name
            or getattr(table, "location", None)
            != self.policy.location
            or getattr(table, "table_type", None) != "TABLE"
            or getattr(
                table,
                "external_data_configuration",
                None,
            )
            is not None
            or labels.get("datarise") != "warehouse"
            or labels.get("environment") != "development"
            or labels.get("workspace") != str(workspace_id)
        ):
            raise ProviderError(ErrorCode.PERMISSION_DENIED)

    def _verify_tables(self, query: CompiledQuery):
        for name in query.physical_tables:
            self._verify_table(name, query.workspace_id)

    def _labels(
        self,
        query: CompiledQuery,
        reservation_id: int | None = None,
    ):
        labels = {
            "datarise": "query",
            "workspace": str(query.workspace_id),
            "intent": query.intent_sha256[:63],
        }

        if reservation_id is not None:
            labels["reservation"] = str(reservation_id)

        return labels

    def _dry_config(self, query: CompiledQuery):
        return bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            use_legacy_sql=False,
            labels=self._labels(query),
        )

    def _execute_config(
        self,
        query: CompiledQuery,
        permit: ExecutionPermit,
        execution_cap: int,
    ):
        return bigquery.QueryJobConfig(
            dry_run=False,
            use_query_cache=False,
            use_legacy_sql=False,
            maximum_bytes_billed=execution_cap,
            priority="INTERACTIVE",
            job_timeout_ms=self.policy.timeout_seconds * 1000,
            labels=self._labels(
                query,
                permit.reservation_id,
            ),
        )

    def _verify_dry_job(
        self,
        query: CompiledQuery,
        job,
    ):
        if _statement_type(job) != "SELECT":
            raise ProviderError(ErrorCode.PERMISSION_DENIED)

        references = _references(job)

        if (
            len(references) != len(query.physical_tables)
            or set(references) != set(query.physical_tables)
        ):
            raise ProviderError(ErrorCode.PERMISSION_DENIED)

        if _referenced_routines(job):
            raise ProviderError(ErrorCode.PERMISSION_DENIED)

        if _referenced_views(job):
            raise ProviderError(ErrorCode.PERMISSION_DENIED)

    def estimate(
        self,
        query: CompiledQuery,
    ) -> QueryEstimate:
        self._verify_query_scope(query)
        self._verify_tables(query)

        try:
            job = self.client.query(
                query.sql,
                job_config=self._dry_config(query),
                location=self.policy.location,
                retry=None,
                timeout=self.policy.timeout_seconds,
            )
        except Exception as error:
            raise classify(error) from None

        self._verify_dry_job(query, job)

        processed = getattr(
            job,
            "total_bytes_processed",
            None,
        )

        confidence = _accuracy(job)

        if type(processed) is not int or processed < 0:
            return QueryEstimate(
                None,
                EstimateConfidence.UNKNOWN,
            )

        # BigQuery on-demand billing has a minimum billed amount per
        # referenced table. Reserve at least that amount so the durable
        # software budget cannot under-reserve a tiny query.
        conservative = max(
            processed,
            (
                BIGQUERY_MINIMUM_BILLED_BYTES_PER_TABLE
                * len(query.physical_tables)
            ),
        )

        return QueryEstimate(
            conservative,
            confidence,
        )

    def _job_id(
        self,
        query: CompiledQuery,
        permit: ExecutionPermit,
    ):
        return (
            f"dr_query_w{query.workspace_id}"
            f"_r{permit.reservation_id}"
            f"_{query.request_token[:32]}"
        )

    def execute(
        self,
        query: CompiledQuery,
        permit: ExecutionPermit,
    ) -> QueryExecutionResult:
        permit.authorize(
            query,
            provider=self.identity,
        )

        self._verify_query_scope(query)
        current = self.estimate(query)

        if (
            current.bytes_processed is None
            or current.confidence
            not in (
                EstimateConfidence.EXACT,
                EstimateConfidence.UPPER_BOUND,
            )
        ):
            raise ProviderError(
                ErrorCode.COST_LIMIT_EXCEEDED
            )

        execution_cap = min(
            permit.reserved_bytes,
            permit.maximum_bytes_billed,
            self.policy.maximum_bytes,
        )

        if (
            execution_cap < 1
            or current.bytes_processed > execution_cap
        ):
            raise ProviderError(
                ErrorCode.COST_LIMIT_EXCEEDED
            )

        job_id = self._job_id(query, permit)
        started = time.monotonic()

        try:
            job = self.client.query(
                query.sql,
                job_config=self._execute_config(
                    query,
                    permit,
                    execution_cap,
                ),
                location=self.policy.location,
                job_id=job_id,
                job_retry=None,
                retry=None,
                timeout=self.policy.timeout_seconds,
            )

            iterator = job.result(
                timeout=self.policy.timeout_seconds,
                retry=None,
            )

            rows = []
            payload_bytes = 0
            truncated = False

            for row in islice(
                iterator,
                permit.output_rows + 1,
            ):
                if len(rows) >= permit.output_rows:
                    truncated = True
                    break

                value = dict(row)

                encoded = json.dumps(
                    value,
                    ensure_ascii=False,
                    default=str,
                    separators=(",", ":"),
                ).encode("utf-8")

                if (
                    payload_bytes + len(encoded)
                    > MAX_RESULT_PAYLOAD_BYTES
                ):
                    truncated = True
                    break

                payload_bytes += len(encoded)
                rows.append(value)

        except ProviderError:
            raise
        except Exception as error:
            # Once the execution request is submitted, failure does not prove
            # that BigQuery did not run it. Durable EXECUTING state must be
            # reconciled rather than blindly released/retried.
            raise classify(
                error,
                uncertain=True,
            ) from None

        processed = getattr(
            job,
            "total_bytes_processed",
            None,
        )

        billed = getattr(
            job,
            "total_bytes_billed",
            None,
        )

        if (
            type(processed) is not int
            or processed < 0
            or type(billed) is not int
            or billed < 0
        ):
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT,
                uncertain=True,
            )

        duration_ms = int(
            (time.monotonic() - started) * 1000
        )

        return QueryExecutionResult(
            provider_job_id=job.job_id,
            rows=tuple(rows),
            truncated=truncated,
            bytes_processed=processed,
            bytes_billed=billed,
            payload_bytes=payload_bytes,
            duration_ms=duration_ms,
        )
    def _duration_ms(self, job) -> int:
        started = getattr(job, "started", None)
        ended = getattr(job, "ended", None)

        if (
            isinstance(started, datetime)
            and isinstance(ended, datetime)
            and ended >= started
        ):
            return int(
                (ended - started).total_seconds() * 1000
            )

        return 0

    def _verify_recovered_job(
        self,
        query: CompiledQuery,
        permit: ExecutionPermit,
        job,
    ):
        expected_id = self._job_id(
            query,
            permit,
        )

        if getattr(job, "job_id", None) != expected_id:
            raise ProviderError(
                ErrorCode.JOB_CONFLICT,
                uncertain=True,
            )

        if (
            getattr(job, "location", None)
            != self.policy.location
        ):
            raise ProviderError(
                ErrorCode.JOB_CONFLICT,
                uncertain=True,
            )

        project = getattr(
            job,
            "project",
            None,
        )

        if (
            project is not None
            and project != self.policy.project
        ):
            raise ProviderError(
                ErrorCode.JOB_CONFLICT,
                uncertain=True,
            )

        labels = getattr(
            job,
            "labels",
            {},
        ) or {}

        expected_labels = self._labels(
            query,
            permit.reservation_id,
        )

        if any(
            labels.get(key) != value
            for key, value in expected_labels.items()
        ):
            raise ProviderError(
                ErrorCode.JOB_CONFLICT,
                uncertain=True,
            )

    def recover(
        self,
        query: CompiledQuery,
        permit: ExecutionPermit,
    ) -> QueryRecovery:
        permit.authorize(
            query,
            provider=self.identity,
        )

        self._verify_query_scope(query)

        job_id = self._job_id(
            query,
            permit,
        )

        try:
            job = self._call(
                self.client.get_job,
                job_id,
                project=self.policy.project,
                location=self.policy.location,
            )
        except ProviderError as error:
            if error.code == ErrorCode.RESOURCE_NOT_FOUND:
                return QueryRecovery(
                    state=RecoveryState.NOT_FOUND,
                    provider_job_id=job_id,
                )

            raise

        self._verify_recovered_job(
            query,
            permit,
            job,
        )

        state = getattr(
            job,
            "state",
            None,
        )

        if state in ("PENDING", "RUNNING"):
            return QueryRecovery(
                state=RecoveryState.IN_PROGRESS,
                provider_job_id=job_id,
            )

        if state != "DONE":
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT,
                uncertain=True,
            )

        duration_ms = self._duration_ms(job)

        if getattr(job, "error_result", None) is not None:
            processed = getattr(
                job,
                "total_bytes_processed",
                None,
            )

            billed = getattr(
                job,
                "total_bytes_billed",
                None,
            )

            if (
                type(processed) is not int
                or processed < 0
            ):
                processed = None

            if (
                type(billed) is not int
                or billed < 0
            ):
                billed = None

            return QueryRecovery(
                state=RecoveryState.FAILED,
                provider_job_id=job_id,
                bytes_processed=processed,
                bytes_billed=billed,
                duration_ms=duration_ms,
            )

        try:
            iterator = job.result(
                timeout=self.policy.timeout_seconds,
                retry=None,
            )

            rows = []
            payload_bytes = 0
            truncated = False

            for row in islice(
                iterator,
                permit.output_rows + 1,
            ):
                if len(rows) >= permit.output_rows:
                    truncated = True
                    break

                value = dict(row)

                encoded = json.dumps(
                    value,
                    ensure_ascii=False,
                    default=str,
                    separators=(",", ":"),
                ).encode("utf-8")

                if (
                    payload_bytes + len(encoded)
                    > MAX_RESULT_PAYLOAD_BYTES
                ):
                    truncated = True
                    break

                payload_bytes += len(encoded)
                rows.append(value)

        except Exception as error:
            raise classify(
                error,
                uncertain=True,
            ) from None

        processed = getattr(
            job,
            "total_bytes_processed",
            None,
        )

        billed = getattr(
            job,
            "total_bytes_billed",
            None,
        )

        if (
            type(processed) is not int
            or processed < 0
            or type(billed) is not int
            or billed < 0
        ):
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT,
                uncertain=True,
            )

        result = QueryExecutionResult(
            provider_job_id=job_id,
            rows=tuple(rows),
            truncated=truncated,
            bytes_processed=processed,
            bytes_billed=billed,
            payload_bytes=payload_bytes,
            duration_ms=duration_ms,
        )

        return QueryRecovery(
            state=RecoveryState.SUCCEEDED,
            provider_job_id=job_id,
            result=result,
            bytes_processed=processed,
            bytes_billed=billed,
            duration_ms=duration_ms,
        )

    