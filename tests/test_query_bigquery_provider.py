"""Cloud-free tests for governed BigQuery analytical reads."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import NotFound
from app.query_governance.providers.base import (
    CompiledQuery,
    ExecutionPermit,
    RecoveryState,
)
from app.query_governance.providers.bigquery import (
    BIGQUERY_MINIMUM_BILLED_BYTES_PER_TABLE,
    BigQueryReadProvider,
)
from app.query_governance.types import EstimateConfidence
from app.warehouse.providers.base import (
    ErrorCode,
    Policy,
    ProviderError,
)


MIB = 1024 * 1024
TABLE = "synthetic-project.dr_dev_w1.m1_v1_g1"


class Table:
    def __init__(self, name=TABLE):
        project, dataset, table = name.split(".")

        self.project = project
        self.dataset_id = dataset
        self.table_id = table
        self.full_table_id = (
            f"{project}:{dataset}.{table}"
        )
        self.location = "asia-south1"
        self.table_type = "TABLE"
        self.external_data_configuration = None
        self.labels = {
            "datarise": "warehouse",
            "environment": "development",
            "workspace": "1",
            "model": "1",
            "generation": "1",
        }


class Job:
    def __init__(
        self,
        *,
        job_id="synthetic-job",
        estimate=100 * MIB,
        billed=None,
        accuracy="PRECISE",
        references=(TABLE,),
        rows=(),
        statement_type="SELECT",
        routines=(),
        views=(),
    ):
        self.job_id = job_id
        self.total_bytes_processed = estimate
        self.total_bytes_billed = (
            estimate if billed is None else billed
        )
        self.referenced_tables = tuple(
            {
                "projectId": name.split(".")[0],
                "datasetId": name.split(".")[1],
                "tableId": name.split(".")[2],
            }
            for name in references
        )
        self.referenced_routines = routines
        self.referenced_logical_views = views
        self.statement_type = statement_type
        self._rows = tuple(rows)

        self._properties = {
            "statistics": {
                "query": {
                    "totalBytesProcessedAccuracy": accuracy,
                }
            }
        }

    def result(self, **kwargs):
        return iter(deepcopy(self._rows))


class Client:
    project = "synthetic-project"

    def __init__(self):
        self.table = Table()
        self.estimate = 100 * MIB
        self.accuracy = "PRECISE"
        self.references = (TABLE,)
        self.statement_type = "SELECT"
        self.routines = ()
        self.views = ()
        self.rows = ()
        self.billed = None
        self.calls = []
        self.jobs = {}

    def get_table(self, name, **kwargs):
        self.calls.append(
            ("get_table", name, kwargs)
        )

        if name != TABLE:
            raise AssertionError(
                "Unexpected physical table"
            )

        return deepcopy(self.table)

    def query(
        self,
        sql,
        *,
        job_config,
        location,
        job_id=None,
        **kwargs,
    ):
        self.calls.append(
            (
                "dry" if job_config.dry_run else "execute",
                sql,
                job_config,
                job_id,
                location,
                kwargs,
            )
        )

        assert location == "asia-south1"
        assert not job_config.use_query_cache
        assert not job_config.use_legacy_sql

        job = Job(
            job_id=job_id or "dry-run",
            estimate=self.estimate,
            billed=self.billed,
            accuracy=self.accuracy,
            references=self.references,
            rows=() if job_config.dry_run else self.rows,
            statement_type=self.statement_type,
            routines=self.routines,
            views=self.views,
        )

        if not job_config.dry_run:
            job.state = "DONE"
            job.location = location
            job.project = self.project
            job.labels = dict(job_config.labels or {})
            job.error_result = None
            self.jobs[job.job_id] = job

        return job
    def get_job(
            self,
            job_id,
            *,
            project=None,
            location=None,
            **kwargs,
    ):
        self.calls.append(
            ("get_job", job_id, project, location, kwargs)
        )

        if job_id not in self.jobs:
            raise NotFound("synthetic missing job")

        return deepcopy(self.jobs[job_id])

    def compiled(**updates):
        value = dict(
            workspace_id=1,
            request_token="a" * 64,
            intent_sha256="b" * 64,
            sql=f"SELECT id FROM `{TABLE}`",
            physical_tables=(TABLE,),
        )
        value.update(updates)
        return CompiledQuery(**value)

def compiled(**updates):
    value = dict(
        workspace_id=1,
        request_token="a" * 64,
        intent_sha256="b" * 64,
        sql=f"SELECT id FROM `{TABLE}`",
        physical_tables=(TABLE,),
    )
    value.update(updates)
    return CompiledQuery(**value)
def permit(**updates):
    value = dict(
        reservation_id=1,
        workspace_id=1,
        provider="BIGQUERY",
        request_token="a" * 64,
        intent_sha256="b" * 64,
        reserved_bytes=100 * MIB,
        maximum_bytes_billed=256 * MIB,
        output_rows=500,
    )
    value.update(updates)
    return ExecutionPermit(**value)


def provider(client=None):
    return BigQueryReadProvider(
        Policy(
            "synthetic-project",
            enabled=True,
        ),
        client or Client(),
    )


@pytest.mark.parametrize('changed_label', [None, 'model', 'generation', 'publication', 'revision'])
def test_publication_table_labels_are_independently_pinned(changed_label):
    name = 'synthetic-project.dr_dev_w1.m20_v30_g1_p50_r7'
    client = Client()
    client.table = Table(name)
    client.table.labels.update(model='20', generation='1', publication='50', revision='7')
    client.references = (name,)
    client.get_table = lambda requested, **kwargs: deepcopy(client.table)
    request = CompiledQuery(1, 'a'*64, 'b'*64, f'SELECT COUNT(*) FROM `{name}`', (name,))
    if changed_label:
        client.table.labels[changed_label] = '99'
        with pytest.raises(ProviderError): provider(client).estimate(request)
    else:
        assert provider(client).estimate(request).confidence == EstimateConfidence.EXACT


def test_precise_estimate_and_minimum_billing_floor():
    client = Client()
    client.estimate = 128

    result = provider(client).estimate(compiled())

    assert (
        result.bytes_processed
        == BIGQUERY_MINIMUM_BILLED_BYTES_PER_TABLE
    )
    assert result.confidence == EstimateConfidence.EXACT

    dry = next(
        call for call in client.calls
        if call[0] == "dry"
    )

    assert dry[2].dry_run
    assert dry[2].maximum_bytes_billed is None


@pytest.mark.parametrize(
    "accuracy,expected",
    [
        ("PRECISE", EstimateConfidence.EXACT),
        (
            "UPPER_BOUND",
            EstimateConfidence.UPPER_BOUND,
        ),
        (
            "LOWER_BOUND",
            EstimateConfidence.LOWER_BOUND,
        ),
        ("UNKNOWN", EstimateConfidence.UNKNOWN),
        (None, EstimateConfidence.UNKNOWN),
    ],
)
def test_dry_run_accuracy_mapping(
    accuracy,
    expected,
):
    client = Client()
    client.accuracy = accuracy

    result = provider(client).estimate(compiled())

    assert result.confidence == expected


@pytest.mark.parametrize(
    "mutation",
    [
        "other_table",
        "routine",
        "view",
        "statement",
        "external",
        "labels",
        "location",
    ],
)
def test_untrusted_physical_scope_fails_closed(mutation):
    client = Client()

    if mutation == "other_table":
        client.references = (
            "synthetic-project.dr_dev_w1.m2_v1_g1",
        )
    elif mutation == "routine":
        client.routines = ({"routineId": "x"},)
    elif mutation == "view":
        client.views = ({"tableId": "v"},)
    elif mutation == "statement":
        client.statement_type = "SCRIPT"
    elif mutation == "external":
        client.table.external_data_configuration = {}
    elif mutation == "labels":
        client.table.labels = {}
    elif mutation == "location":
        client.table.location = "US"

    with pytest.raises(ProviderError):
        provider(client).estimate(compiled())


def test_provider_project_must_match_compiled_tables():
    other = CompiledQuery(
        workspace_id=1,
        request_token="a" * 64,
        intent_sha256="b" * 64,
        sql=(
            "SELECT id FROM "
            "`another-project.dr_dev_w1.m1_v1_g1`"
        ),
        physical_tables=(
            "another-project.dr_dev_w1.m1_v1_g1",
        ),
    )

    with pytest.raises(
        ProviderError,
        match=ErrorCode.PERMISSION_DENIED.value,
    ):
        provider().estimate(other)


def test_execution_uses_reserved_bytes_not_automatic_ceiling():
    client = Client()
    client.estimate = 40 * MIB
    client.billed = 45 * MIB
    client.rows = (
        {"id": "A"},
        {"id": "B"},
    )

    result = provider(client).execute(
        compiled(),
        permit(
            reserved_bytes=50 * MIB,
            maximum_bytes_billed=256 * MIB,
        ),
    )

    executions = [
        call
        for call in client.calls
        if call[0] == "execute"
    ]

    assert len(executions) == 1

    config = executions[0][2]

    assert config.maximum_bytes_billed == 50 * MIB
    assert int(config.job_timeout_ms) == 60_000
    assert result.bytes_processed == 40 * MIB
    assert result.bytes_billed == 45 * MIB
    assert result.row_count == 2


def test_cost_drift_above_reservation_blocks_before_execution():
    client = Client()
    client.estimate = 60 * MIB

    with pytest.raises(
        ProviderError,
        match=ErrorCode.COST_LIMIT_EXCEEDED.value,
    ):
        provider(client).execute(
            compiled(),
            permit(reserved_bytes=50 * MIB),
        )

    assert not any(
        call[0] == "execute"
        for call in client.calls
    )


def test_lower_bound_never_executes():
    client = Client()
    client.accuracy = "LOWER_BOUND"

    with pytest.raises(
        ProviderError,
        match=ErrorCode.COST_LIMIT_EXCEEDED.value,
    ):
        provider(client).execute(
            compiled(),
            permit(),
        )

    assert not any(
        call[0] == "execute"
        for call in client.calls
    )


def test_result_rows_are_bounded_and_marked_truncated():
    client = Client()
    client.estimate = 20 * MIB
    client.rows = (
        {"id": "A"},
        {"id": "B"},
        {"id": "C"},
    )

    result = provider(client).execute(
        compiled(),
        permit(
            reserved_bytes=20 * MIB,
            output_rows=2,
        ),
    )

    assert result.row_count == 2
    assert result.truncated
    assert result.rows == (
        {"id": "A"},
        {"id": "B"},
    )


def test_execution_job_identity_is_deterministic():
    client = Client()
    client.estimate = 20 * MIB

    result = provider(client).execute(
        compiled(),
        permit(reserved_bytes=20 * MIB),
    )

    assert result.provider_job_id == (
        "dr_query_w1_r1_"
        + ("a" * 32)
    )

    execution = next(
        call for call in client.calls
        if call[0] == "execute"
    )

    assert execution[3] == result.provider_job_id
    assert (
        execution[2].labels["reservation"]
        == "1"
    )


def test_disabled_provider_never_constructs_cloud_client():
    with pytest.raises(
        ProviderError,
        match=ErrorCode.DISABLED.value,
    ):
        BigQueryReadProvider(
            Policy("synthetic-project")
        )

def test_recovery_not_found_never_blindly_executes():
    client = Client()
    p = provider(client)

    observation = p.recover(
        compiled(),
        permit(),
    )

    assert observation.state == RecoveryState.NOT_FOUND
    assert not any(
        call[0] == "execute"
        for call in client.calls
    )


def test_recovery_running_stays_in_progress():
    client = Client()
    p = provider(client)
    q = compiled()
    token = permit()

    job_id = p._job_id(q, token)

    job = Job(job_id=job_id)
    job.state = "RUNNING"
    job.location = "asia-south1"
    job.project = "synthetic-project"
    job.labels = p._labels(q, token.reservation_id)

    client.jobs[job_id] = job

    observation = p.recover(q, token)

    assert observation.state == RecoveryState.IN_PROGRESS


def test_recovery_done_failure_returns_accounting_evidence():
    client = Client()
    p = provider(client)
    q = compiled()
    token = permit()

    job_id = p._job_id(q, token)

    job = Job(
        job_id=job_id,
        estimate=40 * MIB,
        billed=50 * MIB,
    )
    job.state = "DONE"
    job.location = "asia-south1"
    job.project = "synthetic-project"
    job.labels = p._labels(q, token.reservation_id)
    job.error_result = {"reason": "synthetic"}

    client.jobs[job_id] = job

    observation = p.recover(q, token)

    assert observation.state == RecoveryState.FAILED
    assert observation.bytes_processed == 40 * MIB
    assert observation.bytes_billed == 50 * MIB


def test_recovery_done_success_reconstructs_bounded_result():
    client = Client()
    p = provider(client)
    q = compiled()
    token = permit(output_rows=2)

    job_id = p._job_id(q, token)

    job = Job(
        job_id=job_id,
        estimate=20 * MIB,
        billed=20 * MIB,
        rows=(
            {"id": "A"},
            {"id": "B"},
            {"id": "C"},
        ),
    )
    job.state = "DONE"
    job.location = "asia-south1"
    job.project = "synthetic-project"
    job.labels = p._labels(q, token.reservation_id)

    client.jobs[job_id] = job

    observation = p.recover(q, token)

    assert observation.state == RecoveryState.SUCCEEDED
    assert observation.result.row_count == 2
    assert observation.result.truncated


def test_recovery_conflicting_job_identity_fails_closed():
    client = Client()
    p = provider(client)
    q = compiled()
    token = permit()

    job_id = p._job_id(q, token)

    job = Job(job_id=job_id)
    job.state = "DONE"
    job.location = "US"
    job.project = "synthetic-project"
    job.labels = p._labels(q, token.reservation_id)

    client.jobs[job_id] = job

    with pytest.raises(
        ProviderError,
        match=ErrorCode.JOB_CONFLICT.value,
    ):
        p.recover(q, token)
