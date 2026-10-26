"""Stateful SDK boundary fake: separate tables/jobs, rollback and lost responses."""
from copy import deepcopy
from types import SimpleNamespace
from google.api_core.exceptions import NotFound, BadRequest, Conflict
from google.cloud import bigquery


class Job:
    def __init__(self, job_id='dry', rows=(), bytes_processed=128, labels=None, location='asia-south1'):
        self.job_id, self.rows = job_id, rows
        self.total_bytes_processed, self.total_bytes_billed = bytes_processed, bytes_processed
        self.location, self.labels, self.state = location, labels or {}, 'DONE'
        self.started = self.ended = None
        self.num_dml_affected_rows = None
        self.project = 'synthetic-project'
        self.job_type = 'query'
        self.error_result = None

    def result(self, **kwargs):
        return iter(deepcopy(self.rows))


class Client:
    project = 'synthetic-project'

    def __init__(self):
        self.datasets, self.tables, self.rows, self.jobs = {}, {}, {}, {}
        self.calls = []
        self.estimate = 128
        self.lose_response = self.fail_transaction = False
        self.lose_load_response = self.fail_load = False

    def get_dataset(self, name, **kwargs):
        if name not in self.datasets:
            raise NotFound('synthetic')
        return deepcopy(self.datasets[name])

    def create_dataset(self, dataset, **kwargs):
        self.datasets[dataset.project + '.' + dataset.dataset_id] = deepcopy(dataset)
        return dataset

    def get_table(self, name, **kwargs):
        if name not in self.tables:
            raise NotFound('synthetic')
        return deepcopy(self.tables[name])

    def create_table(self, table, **kwargs):
        name = '.'.join((table.project, table.dataset_id, table.table_id))
        table._properties.update(location='asia-south1', type='TABLE', id=name.replace('.', ':', 1))
        self.tables[name] = deepcopy(table)
        self.rows[name] = []
        return table

    def get_job(self, job_id, **kwargs):
        if job_id not in self.jobs:
            raise NotFound('synthetic')
        return self.jobs[job_id]

    def load_table_from_json(
        self,
        rows,
        destination,
        *,
        job_id,
        job_config,
        **kwargs,
    ):
        assert kwargs["location"] == "asia-south1"
        assert kwargs["project"] == "synthetic-project"
        assert kwargs["num_retries"] == 0
        assert kwargs["timeout"] > 0
        assert "retry" not in kwargs
        assert job_config.write_disposition == 'WRITE_EMPTY'
        if job_id in self.jobs:
            raise Conflict('synthetic duplicate job')
        if self.rows[destination] or self.fail_load:
            raise BadRequest('synthetic immutable load rejected')
        assert job_config.create_disposition == 'CREATE_NEVER'
        self.calls.append(('load', destination, job_config))
        self.rows[destination] = deepcopy(rows)
        self.jobs[job_id] = Job(job_id, labels=job_config.labels)
        self.jobs[job_id].job_type = 'load'
        self.jobs[job_id].destination = bigquery.TableReference.from_string(destination)
        if self.lose_load_response:
            self.lose_load_response = False
            raise ConnectionError('synthetic committed load response lost')
        return self.jobs[job_id]

    def query(self, sql, *, job_config, job_id=None, job_id_prefix=None, **kwargs):
        import re
        assert kwargs['location'] == 'asia-south1' and kwargs['retry'] is None
        assert not job_config.use_legacy_sql and not job_config.use_query_cache
        assert job_config.maximum_bytes_billed > 0
        self.calls.append(('dry' if job_config.dry_run else 'query', sql, job_config))
        if job_config.dry_run:
            return Job(bytes_processed=self.estimate)
        params = {p.name: p.value for p in job_config.query_parameters}
        names = re.findall(r'`([^`]+)`', sql)
        result = []
        if sql.startswith('BEGIN TRANSACTION'):
            if 'DELETE FROM' in sql:
                raise AssertionError('Mutable serving replacement is not supported')
            if self.fail_transaction:
                raise BadRequest('synthetic controlled statement failure')
            journal, candidate, _, stage, _ = names
            assert not self.rows[candidate]
            assert not any(row['application_token'] == params['application_token'] for row in self.rows[journal])
            self.rows[candidate] = deepcopy(self.rows[stage])
            self.rows[journal].append(deepcopy(params))
        elif sql.startswith('SELECT *'):
            result = deepcopy(self.rows[names[0]])
            if 'WHERE application_token=' in sql:
                result = [r for r in result if r['application_token'] == params['application_token']]
        else:
            raise AssertionError('Unexpected execution SQL')
        key = job_id or job_id_prefix + str(len(self.calls))
        job = Job(key, result, labels=job_config.labels)
        self.jobs[key] = job
        if sql.startswith('BEGIN TRANSACTION') and self.lose_response:
            self.lose_response = False
            raise ConnectionError('synthetic lost response after committed transaction')
        return job

    def delete_table(self, name, **kwargs):
        self.tables.pop(name, None)
        self.rows.pop(name, None)
