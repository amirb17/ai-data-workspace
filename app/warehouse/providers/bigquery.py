"""Explicit bounded BigQuery candidates. No startup, consumer or credential hook."""
from dataclasses import dataclass
import json
import re
from itertools import islice
from app.warehouse.serving_materialization import (
    ServingMaterializationRequest,
    ServingMaterializationResult,
)
from google.api_core import exceptions as google_errors
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.cloud import bigquery

from app.warehouse.accepted_changes import digest
from app.warehouse.providers import model_adapter
from app.warehouse.providers.base import Binding, ErrorCode, Policy, ProviderError, TokenState
from app.warehouse.providers.model_adapter import Plan
from app.warehouse.reliability import Result

JOURNAL = (('application_token', 'STRING', 'REQUIRED'), ('intent_sha256', 'STRING', 'REQUIRED'),
           ('plan_sha256', 'STRING', 'REQUIRED'), ('scope_sha256', 'STRING', 'REQUIRED'),
           ('evidence', 'STRING', 'REQUIRED'))


def classify(error, *, uncertain=False):
    reasons = {e.get('reason') for e in getattr(error, 'errors', ()) or ()}
    if isinstance(error, (DefaultCredentialsError, RefreshError, google_errors.Unauthorized)):
        code = ErrorCode.AUTHENTICATION_FAILED
    elif reasons & {'rateLimitExceeded', 'jobRateLimitExceeded'}:
        code = ErrorCode.RATE_LIMITED
    elif reasons & {'quotaExceeded', 'billingTierLimitExceeded'}:
        code = ErrorCode.QUOTA_EXCEEDED
    elif reasons & {'billingNotEnabled', 'accessDenied'} or isinstance(error, google_errors.Forbidden):
        code = ErrorCode.PERMISSION_DENIED
    elif isinstance(error, google_errors.NotFound):
        code = ErrorCode.RESOURCE_NOT_FOUND
    elif isinstance(error, google_errors.Conflict):
        code = ErrorCode.JOB_CONFLICT
    elif isinstance(error, (google_errors.TooManyRequests,)):
        code = ErrorCode.RATE_LIMITED
    elif isinstance(error, (google_errors.ServerError, TimeoutError, ConnectionError)):
        code = ErrorCode.RETRYABLE_PROVIDER_ERROR
    else:
        code = ErrorCode.PERMANENT_PROVIDER_ERROR
    return ProviderError(code, uncertain=uncertain)


@dataclass(frozen=True)
class Batch:
    binding: Binding
    operation_id: int
    application_token: str
    intent_sha256: str
    plan: Plan
    operation_type: str = 'INITIAL_LOAD'

    def __post_init__(self):
        if type(self.operation_id) is not int or self.operation_id < 1:
            raise ValueError('Operation ID required')
        if self.operation_type not in ('INITIAL_LOAD', 'INCREMENTAL', 'REPLAY', 'BACKFILL', 'REBUILD'):
            raise ValueError('Unknown operation type')
        if any(not re.fullmatch('[a-f0-9]{64}', x) for x in (self.application_token, self.intent_sha256)):
            raise ValueError('Pinned 11E identity required')
        c = self.plan.context
        if (c.model_id, c.model_version_id) != (self.binding.model_id, self.binding.version_id):
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        if any(m['pin']['workspace_id'] != self.binding.workspace_id for m in self.plan.manifests):
            raise ProviderError(ErrorCode.PERMISSION_DENIED)
        # Recompute the approved planners, rejecting hand-assembled altered plans.
        if (
            model_adapter.compile_plan(
                c,
                self.plan.manifests,
                self.plan.before,
            )
            != self.plan
        ):
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)

    @property
    def scope_hash(self):
        return digest([self.binding.__dict__, self.operation_id])

    @property
    def evidence(self):
        return dict(application_token=self.application_token, intent_sha256=self.intent_sha256,
                    plan_sha256=self.plan.checksum, scope_sha256=self.scope_hash,
                    evidence=json.dumps([[m['manifest_id'], m['checksum']] for m in self.plan.manifests], separators=(',', ':')))


class BigQueryTarget:
    def __init__(self, policy: Policy, binding: Binding, client=None):
        if not policy.enabled:
            raise ProviderError(ErrorCode.DISABLED)
        if (policy.project, policy.location) != (binding.project, binding.location):
            raise ProviderError(ErrorCode.LOCATION_MISMATCH)
        self.policy, self.binding = policy, binding
        try:
            self.client = client if client is not None else bigquery.Client(project=policy.project, location=policy.location)
        except Exception as error:
            raise classify(error) from None
        if self.client.project != policy.project:
            raise ProviderError(ErrorCode.PERMISSION_DENIED)
        self.telemetry = []
        self.last_reconciliation = None

    def _call(self, function, *args, **kwargs):
        try:
            return function(*args, retry=None, timeout=self.policy.timeout_seconds, **kwargs)
        except ProviderError:
            raise
        except Exception as error:
            raise classify(error) from None

    def _labels(self, context=None):
        labels = dict(datarise='warehouse', environment=self.binding.environment,
                      workspace=str(self.binding.workspace_id), model=str(self.binding.model_id),
                      generation=str(self.binding.generation))
        if context:
            labels["contract"] = (
                model_adapter.schema_hash(context)[:63]
            )
        return labels

    def _dataset_labels(self):
        return dict(datarise='warehouse', environment='development', workspace=str(self.binding.workspace_id))

    def provision(self, context):
        """Explicit development-only DDL. No call from construction or startup."""
        for dataset in (self.binding.staging, self.binding.serving):
            name = f'{self.binding.project}.{dataset}'
            try:
                found = self._call(self.client.get_dataset, name)
            except ProviderError as error:
                if error.code != ErrorCode.RESOURCE_NOT_FOUND:
                    raise
                spec = bigquery.Dataset(name)
                spec.location, spec.labels = self.policy.location, self._dataset_labels()
                self._call(self.client.create_dataset, spec)
                found = self._call(self.client.get_dataset, name)
            self._verify_dataset(found)
        self._table(self.binding.physical('journal'), JOURNAL, create=True, journal=True)
        self._table(self._serving_table(), model_adapter.schema(context), context=context, create=True)

    def _serving_table(self):
        return f'{self.binding.project}.{self.binding.serving}.{self.binding.table}'

    def _verify_dataset(self, dataset):
        if dataset.location != self.policy.location:
            raise ProviderError(ErrorCode.LOCATION_MISMATCH)
        if (dataset.project != self.binding.project
                or dataset.dataset_id not in (self.binding.staging, self.binding.serving)
                or any(dataset.labels.get(k) != v for k, v in self._dataset_labels().items())
                or dataset.default_table_expiration_ms is not None):
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        if any(e.entity_id in ('allUsers', 'allAuthenticatedUsers') for e in dataset.access_entries):
            raise ProviderError(ErrorCode.PERMISSION_DENIED)

    def _table(
            self,
            name,
            fields,
            *,
            context=None,
            create=False,
            journal=False,
            extra_labels=None,
        ):
        for dataset in (self.binding.staging, self.binding.serving):
            self._verify_dataset(self._call(self.client.get_dataset, f'{self.binding.project}.{dataset}'))
        try:
            expected_labels = (
                self._labels(
                    context
                )
                | (
                    extra_labels
                    or {}
                )
            )
            found = self._call(self.client.get_table, name)
        except ProviderError as error:
            if error.code != ErrorCode.RESOURCE_NOT_FOUND or not create:
                raise
            spec = bigquery.Table(name, schema=[bigquery.SchemaField(*field) for field in fields])
            spec.labels = expected_labels
            if journal:
                spec.clustering_fields = ['application_token']
            self._call(self.client.create_table, spec)
            found = self._call(self.client.get_table, name)
        # SchemaField's positional third argument is mode. Reject policy tags,
        # generated/default columns, nested fields and all implicit evolution.
        def normalized(field):
            representation = field.to_api_repr()
            representation['type'] = {'INTEGER': 'INT64', 'BOOLEAN': 'BOOL'}.get(representation['type'], representation['type'])
            return representation
        expected_schema = [normalized(bigquery.SchemaField(*field)) for field in fields]
        if found.location != self.policy.location:
            raise ProviderError(ErrorCode.LOCATION_MISMATCH)
        if (found.full_table_id.replace(':', '.') != name or found.table_type != 'TABLE'
                or found.location != self.policy.location
                or [normalized(f) for f in found.schema] != expected_schema
                or found.time_partitioning is not None or found.range_partitioning is not None
                or found.clustering_fields != (['application_token'] if journal else None)
                or found.require_partition_filter or found.expires is not None
                or any(found.labels.get(k) != v for k, v in expected_labels.items())):
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        return found

    def _check(self, batch):
        batch.__post_init__()
        if batch.binding != self.binding:
            raise ProviderError(ErrorCode.PERMISSION_DENIED)
        # The registered serving table is a verified, empty reservation. 11F
        # never promotes candidates into it or makes a current consumer view.
        self._table(self._serving_table(), model_adapter.schema(batch.plan.context), context=batch.plan.context)
        data = model_adapter.rows(batch.plan)
        if (len(data) > self.policy.maximum_rows
                or len(json.dumps(data, ensure_ascii=False).encode()) > self.policy.maximum_payload_bytes):
            raise ProviderError(ErrorCode.COST_LIMIT_EXCEEDED)
        return data

    def _name(self, batch, purpose):
        return self.binding.physical(purpose + '_' + batch.application_token)

    def _job_id(self, batch, purpose):
        return 'dr_' + purpose + '_' + digest([batch.scope_hash, batch.application_token])

    def _params(self, batch):
        return [bigquery.ScalarQueryParameter(k, 'STRING', v) for k, v in batch.evidence.items()]

    def _config(self, batch, *, dry=False):
        return bigquery.QueryJobConfig(dry_run=dry, use_query_cache=False, use_legacy_sql=False,
            maximum_bytes_billed=self.policy.maximum_bytes, priority='INTERACTIVE',
            labels=self._job_labels(batch), query_parameters=self._params(batch))

    def _job_labels(self, batch):
        return self._labels() | dict(operation=getattr(batch, 'operation_type', 'REPLAY').lower(),
            intent=digest(batch.intent_sha256)[:63],
            plan=digest(getattr(batch.plan, 'checksum', ['publication-read', batch.application_token]))[:63])
    def _serving_stage(
        self,
        request,
    ):
        return self.binding.physical(
            "serving_"
            + request.materialization_token
        )

    def _serving_job_id(
        self,
        request,
        purpose,
    ):
        if not re.fullmatch(
            r"[a-z0-9_]{1,40}",
            purpose,
        ):
            raise ValueError(
                "Backend serving purpose required"
            )

        return (
            "dr_serving_"
            + purpose
            + "_"
            + digest(
                [
                    request.intent_sha256,
                    purpose,
                ]
            )
        )

    def _serving_job_labels(
        self,
        request,
        purpose,
    ):
        return (
            self._labels(
                request.context
            )
            | {
                "operation": (
                    "materialize"
                ),
                "purpose": purpose[
                    :63
                ],
                "materialization": (
                    request
                    .materialization_token[
                        :63
                    ]
                ),
                "intent": (
                    digest(
                        request.intent_sha256
                    )[
                        :63
                    ]
                ),
            }
        )

    def _serving_config(
        self,
        request,
        purpose,
        *,
        dry=False,
    ):
        return bigquery.QueryJobConfig(
            dry_run=dry,
            use_query_cache=False,
            use_legacy_sql=False,
            maximum_bytes_billed=(
                self.policy.maximum_bytes
            ),
            priority="INTERACTIVE",
            labels=(
                self._serving_job_labels(
                    request,
                    purpose,
                )
            ),
            query_parameters=[],
        )

    def _serving_existing_job(
        self,
        request,
        purpose,
    ):
        job_id = self._serving_job_id(
            request,
            purpose,
        )

        try:
            job = self._call(
                self.client.get_job,
                job_id,
                project=self.policy.project,
                location=self.policy.location,
            )

        except ProviderError as error:
            if (
                error.code
                == ErrorCode.RESOURCE_NOT_FOUND
            ):
                return None

            raise

        expected_labels = (
            self._serving_job_labels(
                request,
                purpose,
            )
        )

        if (
            job.job_id != job_id
            or getattr(job, 'project', None) != self.policy.project
            or getattr(job, 'job_type', None) != 'load'
            or str(getattr(job, 'destination', '')) != request.physical_table
            or job.location
            != self.policy.location
            or any(
                job.labels.get(key)
                != value
                for key, value
                in expected_labels.items()
            )
        ):
            raise ProviderError(
                ErrorCode.JOB_CONFLICT
            )

        return job

    @staticmethod
    def _require_successful_job(
        job,
    ):
        if (
            job.state != "DONE"
            or getattr(
                job,
                "error_result",
                None,
            )
            is not None
        ):
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT,
                uncertain=True,
            )

    def _record_serving_job(
        self,
        job,
        purpose,
        request,
    ):
        self.telemetry.append(
            {
                "job_id": (
                    job.job_id
                ),
                "job_type": (
                    "serving_"
                    + purpose
                ),
                "location": (
                    job.location
                ),
                "materialization_token": (
                    request
                    .materialization_token
                ),
                "publication_id": (
                    request.publication_id
                ),
                "publication_revision": (
                    request
                    .publication_revision
                ),
                "resource_generation": (
                    self.binding.generation
                ),
                "model_id": (
                    self.binding.model_id
                ),
                "started": (
                    str(job.started)
                    if job.started
                    else None
                ),
                "ended": (
                    str(job.ended)
                    if job.ended
                    else None
                ),
                "duration_seconds": (
                    (
                        job.ended
                        - job.started
                    ).total_seconds()
                    if (
                        job.started
                        and job.ended
                    )
                    else None
                ),
                "bytes_processed": (
                    getattr(
                        job,
                        "total_bytes_processed",
                        None,
                    )
                ),
                "bytes_billed": (
                    getattr(
                        job,
                        "total_bytes_billed",
                        None,
                    )
                ),
                "affected_rows": (
                    getattr(
                        job,
                        "num_dml_affected_rows",
                        None,
                    )
                ),
                "retry_count": 0,
            }
        )
    def _check_serving_request(
        self,
        request,
    ):
        if not isinstance(
            request,
            ServingMaterializationRequest,
        ):
            raise ValueError(
                "Typed serving materialization request required"
            )

        # Re-run immutable request validation at the provider boundary.
        request.__post_init__()

        if (
            request.binding
            != self.binding
        ):
            raise ProviderError(
                ErrorCode.PERMISSION_DENIED
            )

        fields = (
            model_adapter.schema(
                request.context
            )
        )

        self._table(
            self._serving_table(),
            fields,
            context=request.context,
        )

        rows = list(
            request.physical_rows
        )

        payload_bytes = len(
            json.dumps(
                rows,
                ensure_ascii=False,
            ).encode(
                "utf-8"
            )
        )

        # V1 provider remains explicitly bounded.
        if (
            len(rows)
            > self.policy.maximum_rows
            or payload_bytes
            > self.policy.maximum_payload_bytes
        ):
            raise ProviderError(
                ErrorCode.COST_LIMIT_EXCEEDED
            )

        return (
            fields,
            rows,
        )

    def _serving_estimate(
        self,
        request,
        sql,
        purpose,
    ):
        job = self._call(
            self.client.query,
            sql,
            job_config=(
                self._serving_config(
                    request,
                    purpose,
                    dry=True,
                )
            ),
            location=(
                self.policy.location
            ),
        )

        estimated = (
            job.total_bytes_processed
        )

        accuracy = (
            getattr(
                job,
                "_properties",
                {},
            )
            .get(
                "statistics",
                {},
            )
            .get(
                "query",
                {},
            )
            .get(
                "totalBytesProcessedAccuracy"
            )
        )

        if accuracy in (
            "UNKNOWN",
            "LOWER_BOUND",
        ):
            raise ProviderError(
                ErrorCode.COST_LIMIT_EXCEEDED
            )

        if (
            type(estimated)
            is not int
            or estimated < 0
            or estimated
            > self.policy.maximum_bytes
        ):
            raise ProviderError(
                ErrorCode.COST_LIMIT_EXCEEDED
            )

        return estimated

    def _serving_read(
        self,
        request,
        table,
        purpose,
    ):
        sql = (
            f"SELECT * FROM `{table}`"
        )

        self._serving_estimate(
            request,
            sql,
            purpose,
        )

        try:
            job = self._call(
                self.client.query,
                sql,
                job_config=(
                    self._serving_config(
                        request,
                        purpose,
                    )
                ),
                location=(
                    self.policy.location
                ),
                job_id_prefix=(
                    self._serving_job_id(
                        request,
                        purpose,
                    )
                    + "_"
                ),
                job_retry=None,
            )

            rows = []
            encoded_bytes = 0

            for row in islice(
                job.result(
                    timeout=(
                        self.policy
                        .timeout_seconds
                    ),
                    retry=None,
                ),
                (
                    self.policy
                    .maximum_rows
                    + 1
                ),
            ):
                encoded_bytes += len(
                    json.dumps(
                        dict(row),
                        ensure_ascii=False,
                        default=str,
                    ).encode(
                        "utf-8"
                    )
                )

                if (
                    len(rows)
                    >= self.policy
                    .maximum_rows
                    or encoded_bytes
                    > self.policy
                    .maximum_payload_bytes
                ):
                    raise ProviderError(
                        ErrorCode
                        .COST_LIMIT_EXCEEDED
                    )

                rows.append(
                    row
                )

            self._record_serving_job(
                job,
                purpose,
                request,
            )

            return rows

        except ProviderError:
            raise

        except Exception as error:
            raise classify(
                error
            ) from None
    def materialize_serving(
        self,
        request,
    ):
        """Materialize one immutable publication snapshot.

        A publication receives a unique serving TABLE. It is never overwritten
        by a later publication, so governed queries remain pinned to the exact
        publication they were compiled against.
        """

        (
            fields,
            rows,
        ) = self._check_serving_request(
            request
        )

        serving = (
            request.physical_table
        )

        labels = (
            self._publication_labels(
                request
            )
        )

        self._table(
            serving,
            fields,
            context=request.context,
            create=True,
            extra_labels=labels,
        )

        load = (
            self._serving_existing_job(
                request,
                "load",
            )
        )

        if load is None:
            if rows:
                config = (
                    bigquery
                    .LoadJobConfig(
                        schema=[
                            bigquery.SchemaField(
                                *field
                            )
                            for field
                            in fields
                        ],
                        write_disposition=(
                            "WRITE_EMPTY"
                        ),
                        create_disposition=(
                            "CREATE_NEVER"
                        ),
                        ignore_unknown_values=False,
                        max_bad_records=0,
                        labels=(
                            self
                            ._serving_job_labels(
                                request,
                                "load",
                            )
                        ),
                    )
                )

                try:
                    load = self._call(
                        self.client
                        .load_table_from_json,
                        rows,
                        serving,
                        job_config=config,
                        job_id=(
                            self._serving_job_id(
                                request,
                                "load",
                            )
                        ),
                        location=(
                            self.policy.location
                        ),
                    )

                except ProviderError as error:
                    raise ProviderError(
                        error.code,
                        uncertain=True,
                    ) from None

        if load is not None:
            try:
                load.result(
                    timeout=(
                        self.policy
                        .timeout_seconds
                    ),
                    retry=None,
                )

            except Exception as error:
                raise classify(
                    error,
                    uncertain=True,
                ) from None

            self._require_successful_job(load)
            # Verify the deterministic job's actual scope after completion too,
            # including on a fresh submission rather than only on recovery.
            load = self._serving_existing_job(request, 'load')
            if load is None:
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT, uncertain=True)
            self._require_successful_job(load)

            self._record_serving_job(
                load,
                "load",
                request,
            )

        # Independently verify immutable publication content.
        actual_rows = (
            self._serving_read(
                request,
                serving,
                "verify_serving",
            )
        )

        actual_states = (
            model_adapter.states(
                request.context,
                actual_rows,
            )
        )

        if (
            actual_states
            != request.states
        ):
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            )

        canonical_rows = tuple(
            model_adapter
            .rows_from_states(
                request.context,
                actual_states,
            )
        )

        actual_sha256 = digest(
            [
                "datarise-serving-state",
                1,
                request.schema_sha256,
                canonical_rows,
            ]
        )

        if (
            actual_sha256
            != request.serving_sha256
            or len(canonical_rows)
            != request.row_count
        ):
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            )

        if load is None:
            # Empty publication snapshot has no load job. Use the deterministic
            # materialization identity as the durable provider reference.
            provider_job_id = (
                "empty_"
                + request
                .materialization_token[
                    :64
                ]
            )

            bytes_processed = 0
            bytes_billed = 0

        else:
            provider_job_id = (
                load.job_id
            )

            bytes_processed = getattr(
                load,
                "total_bytes_processed",
                None,
            )
            if bytes_processed is None:
                bytes_processed = 0

            bytes_billed = getattr(
                load,
                "total_bytes_billed",
                None,
            )
            if bytes_billed is None:
                bytes_billed = 0

            if (
                type(bytes_processed)
                is not int
                or bytes_processed < 0
                or type(bytes_billed)
                is not int
                or bytes_billed < 0
            ):
                raise ProviderError(
                    ErrorCode.TARGET_INCONSISTENT
                )

        return ServingMaterializationResult(
            publication_id=(
                request.publication_id
            ),
            publication_revision=(
                request
                .publication_revision
            ),
            generation=(
                self.binding.generation
            ),
            materialization_token=(
                request
                .materialization_token
            ),
            intent_sha256=(
                request.intent_sha256
            ),
            provider_job_id=(
                provider_job_id
            ),
            serving_sha256=(
                actual_sha256
            ),
            row_count=len(
                canonical_rows
            ),
            bytes_processed=(
                bytes_processed
            ),
            bytes_billed=(
                bytes_billed
            ),
        )
    def _record(self, job, kind, batch):
        self.telemetry.append(dict(job_id=job.job_id, job_type=kind, location=job.location,
            application_token=batch.application_token, operation_id=batch.operation_id,
            operation_type=getattr(batch, 'operation_type', 'REPLAY'),
            resource_generation=self.binding.generation, model_id=self.binding.model_id,
            started=str(job.started) if job.started else None, ended=str(job.ended) if job.ended else None,
            duration_seconds=(job.ended-job.started).total_seconds() if job.started and job.ended else None,
            bytes_processed=getattr(job, 'total_bytes_processed', None), bytes_billed=getattr(job, 'total_bytes_billed', None),
            affected_rows=getattr(job, 'num_dml_affected_rows', None), retry_count=0))

    def _estimate(self, batch, sql):
        job = self._call(self.client.query, sql, job_config=self._config(batch, dry=True), location=self.policy.location)
        estimated = job.total_bytes_processed
        accuracy = getattr(job, '_properties', {}).get('statistics', {}).get('query', {}).get('totalBytesProcessedAccuracy')
        if accuracy in ('UNKNOWN', 'LOWER_BOUND'):
            raise ProviderError(ErrorCode.COST_LIMIT_EXCEEDED)
        if type(estimated) is not int or estimated < 0 or estimated > self.policy.maximum_bytes:
            raise ProviderError(ErrorCode.COST_LIMIT_EXCEEDED)
        return estimated

    def _query(self, batch, sql, purpose):
        self._estimate(batch, sql)
        # Read jobs get fresh IDs; deterministic write identities are handled
        # separately, so reconciliation can never reuse a cached earlier read.
        try:
            job = self._call(self.client.query, sql, job_config=self._config(batch), location=self.policy.location,
                             job_id_prefix=self._job_id(batch, purpose) + '_', job_retry=None)
            answer, encoded_bytes = [], 0
            for row in islice(job.result(timeout=self.policy.timeout_seconds, retry=None), self.policy.maximum_rows + 1):
                encoded_bytes += len(json.dumps(dict(row), ensure_ascii=False, default=str).encode('utf-8'))
                if len(answer) >= self.policy.maximum_rows or encoded_bytes > self.policy.maximum_payload_bytes:
                    raise ProviderError(ErrorCode.COST_LIMIT_EXCEEDED)
                answer.append(row)
            self._record(job, purpose, batch)
            return answer
        except ProviderError:
            raise
        except Exception as error:
            raise classify(error) from None

    def _existing_job(self, batch, purpose):
        try:
            job = self._call(self.client.get_job, self._job_id(batch, purpose), project=self.policy.project,
                             location=self.policy.location)
        except ProviderError as error:
            if error.code == ErrorCode.RESOURCE_NOT_FOUND:
                return None
            raise
        if job.location != self.policy.location or any(job.labels.get(k) != v for k, v in self._job_labels(batch).items()):
            raise ProviderError(ErrorCode.JOB_CONFLICT)
        return job

    def lookup(self, batch):
        self._check(batch)
        journal = self.binding.physical('journal')
        self._table(journal, JOURNAL, journal=True)
        rows = self._query(batch, f'SELECT * FROM `{journal}` WHERE application_token=@application_token', 'lookup')
        if len(rows) > 1 or rows and dict(rows[0]) != batch.evidence:
            return TokenState.INCONSISTENT
        if rows:
            return TokenState.APPLIED
        job = self._existing_job(batch, 'apply')
        if job is None:
            return TokenState.NOT_APPLIED
        if job.state != 'DONE':
            return TokenState.IN_PROGRESS
        # A successful committed script without matching journal is corruption;
        # a failed immutable job ID is never blindly reused for a new execution.
        return TokenState.INCONSISTENT

    def apply(self, batch):
        data = self._check(batch)
        state = self.lookup(batch)
        if state == TokenState.APPLIED:
            return self.reconcile(batch)
        if state != TokenState.NOT_APPLIED:
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT, uncertain=True)
        fields = model_adapter.schema(batch.plan.context)
        stage, candidate = self._name(batch, 'input'), self._name(batch, 'candidate')
        for name in (stage, candidate):
            self._table(name, fields, context=batch.plan.context, create=True)
        load = self._existing_job(batch, 'load')
        if load is None and data:
            config = bigquery.LoadJobConfig(schema=[bigquery.SchemaField(*f) for f in fields],
                write_disposition='WRITE_EMPTY', create_disposition='CREATE_NEVER',
                ignore_unknown_values=False, max_bad_records=0, labels=self._job_labels(batch))
            try:
                load = self._call(self.client.load_table_from_json, data, stage, job_config=config,
                                  job_id=self._job_id(batch, 'load'), location=self.policy.location)
            except ProviderError as error:
                raise ProviderError(error.code, uncertain=True) from None
        if load is not None:
            try:
                load.result(timeout=self.policy.timeout_seconds, retry=None)
                self._record(load, 'load', batch)
            except Exception as error:
                raise classify(error, uncertain=True) from None
        # Independently read typed staging before mutation; a changed input or
        # reused load job cannot silently change a plan under the same token.
        staged = self._query(batch, f'SELECT * FROM `{stage}`', 'validate_input')
        if (
            model_adapter.states(
                batch.plan.context,
                staged,
            )
            != batch.plan.after
        ):
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        journal = self.binding.physical('journal')
        insert = f'INSERT INTO `{candidate}` SELECT * FROM `{stage}`'
        record = f'INSERT INTO `{journal}` VALUES (@application_token,@intent_sha256,@plan_sha256,@scope_sha256,@evidence)'
        checks = [f'SELECT COUNT(*) FROM `{journal}` WHERE application_token=@application_token',
                  f'SELECT COUNT(*) FROM `{candidate}`', insert, record]
        # Script dry-run control-flow estimates are not sufficient. Estimate
        # every scanned/DML statement separately before the transactional script.
        if sum(self._estimate(batch, statement) for statement in checks) > self.policy.maximum_bytes:
            raise ProviderError(ErrorCode.COST_LIMIT_EXCEEDED)
        script = ('BEGIN TRANSACTION;\n'
            f"ASSERT (SELECT COUNT(*) FROM `{journal}` WHERE application_token=@application_token)=0 AS 'token_exists';\n"
            f"ASSERT (SELECT COUNT(*) FROM `{candidate}`)=0 AS 'candidate_exists';\n"
            + insert + ';\n' + record + ';\nCOMMIT TRANSACTION;')
        try:
            job = self._call(self.client.query, script, job_config=self._config(batch),
                job_id=self._job_id(batch, 'apply'), location=self.policy.location, job_retry=None)
            job.result(timeout=self.policy.timeout_seconds, retry=None)
            self._record(job, 'apply', batch)
        except Exception as error:
            # Caller must recover with lookup, including timeout/409/response loss.
            if isinstance(error, ProviderError):
                raise ProviderError(error.code, uncertain=True) from None
            raise classify(error, uncertain=True) from None
        return self.reconcile(batch)

    def reconcile(self, batch):
        self._check(batch)

        if self.lookup(batch) != TokenState.APPLIED:
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT,
                uncertain=True,
            )

        candidate = self._name(
            batch,
            "candidate",
        )

        self._table(
            candidate,
            model_adapter.schema(
                batch.plan.context
            ),
            context=batch.plan.context,
        )

        physical_rows = self._query(
            batch,
            f"SELECT * FROM `{candidate}`",
            "reconcile",
        )

        actual = model_adapter.states(
            batch.plan.context,
            physical_rows,
        )

        if actual != batch.plan.after:
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            )

        checksum = model_adapter.result_checksum(
            batch.plan.context,
            actual,
        )

        self.last_reconciliation = (
            model_adapter.reconciliation(
                batch.plan,
                actual,
            )
            | dict(
                application_token=(
                    batch.application_token
                ),
                resource_generation=(
                    self.binding.generation
                ),
                checksum=checksum,
                passed=True,
            )
        )

        # Reconstruct typed values and identities from the physical candidate.
        # Never trust declared loader counts/checksums as reconciliation evidence.
        expected = batch.plan.result

        return Result(
            **(
                expected.model_dump()
                | dict(
                    checksum=checksum
                )
            )
        )
    def read_published_candidate(self, batch, expected):
        """Called only for a PostgreSQL-published, owned immutable intent.

        Read one bounded candidate, not a whole workspace/dimension. The caller
        resolves its publication lineage; newest affected-member entry wins.
        """
        self._table(self.binding.physical('journal'), JOURNAL, journal=True)
        journal = self._query(batch, f'SELECT * FROM `{self.binding.physical("journal")}` '
                              'WHERE application_token=@application_token', 'read_publication_journal')
        if (len(journal) != 1 or journal[0]['intent_sha256'] != batch.intent_sha256
                or journal[0]['scope_sha256'] != batch.scope_hash
                or journal[0]['application_token'] != batch.application_token
                or (getattr(batch, 'manifest_evidence', None) is not None
                    and journal[0]['evidence'] != batch.manifest_evidence)):
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        name = self._name(batch, 'candidate')
        self._table(name, model_adapter.schema(batch.plan.context), context=batch.plan.context)
        actual = model_adapter.states(batch.plan.context, self._query(batch, f'SELECT * FROM `{name}`', 'read_publication'))
        if (
    model_adapter.result_checksum(
        batch.plan.context,
        actual,
    )
    != expected.checksum
):
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        return actual

    def cleanup_input(self, batch, *, finalized):
        """Explicit terminal cleanup only; candidate/journal evidence is retained."""
        if not finalized or self.lookup(batch) != TokenState.APPLIED:
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        self.reconcile(batch)
        self._call(self.client.delete_table, self._name(batch, 'input'), not_found_ok=True)
    def _publication_labels(
        self,
        request,
    ):
        return {
            "publication": str(
                request.publication_id
            ),
            "revision": str(
                request.publication_revision
            ),
        }
