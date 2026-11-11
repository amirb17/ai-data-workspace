"""Typed orchestration port to independent warehouse worker processes.

No DSN or provider credential is supplied to this adapter. Its caller only
knows the five limited RPC operations; transport signing, replay protection
and worker-specific database access live in separate processes.

This is a boundary adapter, not an authorization grant or full query runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.warehouse.worker_rpc_client import WarehouseWorkerClient
from app.services.warehouse_sql_send_recovery_service import (
    JobIdentity, RecoveryLease, RecoveryView,
)
from app.services.warehouse_sql_attested_accounting_service import AccountingResult
from app.services.warehouse_sql_result_eligibility_service import ResultEligibility


def _job(payload):
    if type(payload) is not dict or set(payload) != {
        "project", "location", "job_id"
    }:
        raise ValueError("UNTRUSTED_WORKER_JOB_ENVELOPE")
    if not all(type(v) is str and v for v in payload.values()):
        raise ValueError("UNTRUSTED_WORKER_JOB_ENVELOPE")
    return JobIdentity(**payload)


def _lease(payload):
    if type(payload) is not dict or set(payload) != {
        "attempt_id", "revision", "generation", "token", "lease_until", "job"
    }:
        raise ValueError("UNTRUSTED_WORKER_LEASE_ENVELOPE")
    if any(type(payload[k]) is not int or payload[k] < 1 for k in
           ("attempt_id", "revision", "generation")):
        raise ValueError("UNTRUSTED_WORKER_LEASE_ENVELOPE")
    until = datetime.fromisoformat(payload["lease_until"])
    if until.tzinfo is None:
        raise ValueError("WORKER_LEASE_TIMEZONE_REQUIRED")
    return RecoveryLease(
        payload["attempt_id"],payload["revision"],payload["generation"],
        UUID(payload["token"]),until,_job(payload["job"]))


def _view(payload):
    required={"attempt_id","revision","phase","classification",
              "revoked","expired","job"}
    if type(payload) is not dict or set(payload) != required:
        raise ValueError("UNTRUSTED_WORKER_VIEW_ENVELOPE")
    if type(payload["revoked"]) is not bool or type(payload["expired"]) is not bool:
        raise ValueError("UNTRUSTED_WORKER_VIEW_ENVELOPE")
    return RecoveryView(
        attempt_id=payload["attempt_id"],revision=payload["revision"],
        phase=payload["phase"],classification=payload["classification"],
        revoked=payload["revoked"],expired=payload["expired"],
        job=_job(payload["job"]))


@dataclass(frozen=True)
class WarehouseWorkerRouter:
    submit: WarehouseWorkerClient
    attest: WarehouseWorkerClient
    account: WarehouseWorkerClient
    results: WarehouseWorkerClient

    def __post_init__(self):
        if (type(self.submit) is not WarehouseWorkerClient
                or type(self.attest) is not WarehouseWorkerClient
                or type(self.account) is not WarehouseWorkerClient
                or type(self.results) is not WarehouseWorkerClient
                or (self.submit.duty,self.attest.duty,self.account.duty,
                    self.results.duty) != (
                        "provider_submission","recovery_attestation",
                        "accounting","result_authorization")):
            raise ValueError("INDEPENDENT_WORKER_ROUTING_REQUIRED")
        if len({x.port for x in (self.submit,self.attest,self.account,self.results)})!=4:
            raise ValueError("DISTINCT_WORKER_PORTS_REQUIRED")
        if len({x.secret for x in (self.submit,self.attest,self.account,self.results)})!=4:
            raise ValueError("DISTINCT_WORKER_RPC_SECRETS_REQUIRED")

    def inspect_recovery(self,attempt_id: int) -> RecoveryView:
        return _view(self.attest.invoke("inspect_recovery",{"attempt_id":attempt_id}))

    def acquire_recovery(self,attempt_id: int,revision: int,seconds: int=30) -> RecoveryLease:
        return _lease(self.attest.invoke("acquire_recovery",{
            "attempt_id":attempt_id,
            "expected_revision":revision,"lease_seconds":seconds,
        }))

    def record_send_boundary(self,lease: RecoveryLease,revision: int) -> RecoveryView:
        from dataclasses import asdict
        data=asdict(lease)
        data["token"]=str(lease.token)
        data["lease_until"]=lease.lease_until.isoformat()
        return _view(self.submit.invoke("record_send_boundary",{
            "lease":data,"expected_revision":revision,
        }))

    def settle_terminal(self,attempt_id: int) -> AccountingResult:
        data=self.account.invoke("settle_terminal",{"attempt_id":attempt_id})
        required=set(AccountingResult.__dataclass_fields__)
        if type(data) is not dict or set(data) != required:
            raise ValueError("UNTRUSTED_WORKER_ACCOUNTING_ENVELOPE")
        return AccountingResult(**data)

    def inspect_eligibility(self,attempt_id: int) -> ResultEligibility:
        data=self.results.invoke("inspect_eligibility",{"attempt_id":attempt_id})
        required=set(ResultEligibility.__dataclass_fields__)
        if type(data) is not dict or set(data) != required:
            raise ValueError("UNTRUSTED_WORKER_RESULT_ENVELOPE")
        return ResultEligibility(**data)

    def probe_workers(self) -> dict:
        """Signed live database-identity probes. No SQL/job arguments accepted."""
        results = {}
        for client in (self.submit, self.attest, self.account, self.results):
            report=client.invoke("health",{})
            if (type(report) is not dict
                    or report != {"duty":client.duty,
                                  "database_identity_verified":True,
                                  "query_execution_enabled":False}):
                raise ValueError("WORKER_IDENTITY_PROBE_FAILED")
            results[client.duty]=True
        return results
