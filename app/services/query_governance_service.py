"""Transactional façade for provider-neutral analytical query governance.

This module owns transaction boundaries only. Admission policy remains pure in
app.query_governance and durable concurrency/accounting remains in the repository.
No analytical consumer or provider is invoked here.
"""

from app.db import query_governance_repository as repository
from app.db.database import repository_transaction
from app.query_governance.policy import QueryPolicy
from app.query_governance.types import QueryEstimate


class QueryGovernanceService:
    def __init__(
        self,
        workspace_id: int,
        user: dict,
        *,
        policy: QueryPolicy | None = None,
    ):
        if type(workspace_id) is not int or workspace_id < 1:
            raise ValueError("Positive workspace id required")

        if (
            not isinstance(user, dict)
            or not isinstance(user.get("owner_key"), str)
            or not user["owner_key"]
        ):
            raise ValueError("Authenticated workspace principal required")

        self.workspace_id = workspace_id
        self.user = user
        self.policy = policy or QueryPolicy.from_environment()

    @property
    def owner_key(self) -> str:
        return self.user["owner_key"]

    def usage(self):
        with repository_transaction() as conn:
            return repository.usage_snapshot(
                conn,
                self.workspace_id,
                self.owner_key,
            )

    def admit(
        self,
        *,
        consumer: str,
        provider: str,
        request_token: str,
        intent_sha256: str,
        estimate: QueryEstimate,
        requested_rows: int | None = None,
        lease_seconds: int = 120,
    ):
        if not isinstance(estimate, QueryEstimate):
            raise ValueError("Typed query estimate required")

        with repository_transaction() as conn:
            return repository.reserve(
                conn,
                workspace_id=self.workspace_id,
                owner_key=self.owner_key,
                consumer=consumer,
                provider=provider,
                request_token=request_token,
                intent_sha256=intent_sha256,
                estimate=estimate,
                requested_rows=requested_rows,
                policy=self.policy,
                lease_seconds=lease_seconds,
            )

    def claim(self, reservation_id: int):
        if type(reservation_id) is not int or reservation_id < 1:
            raise ValueError("Positive reservation id required")

        with repository_transaction() as conn:
            return repository.claim(
                conn,
                reservation_id=reservation_id,
                owner_key=self.owner_key,
            )

    def reservation(self, reservation_id: int):
        if type(reservation_id) is not int or reservation_id < 1:
            raise ValueError('Positive reservation id required')
        with repository_transaction() as conn:
            return repository.read_owned_reservation(conn, workspace_id=self.workspace_id,
                owner_key=self.owner_key, reservation_id=reservation_id)

    def complete(
        self,
        reservation_id: int,
        *,
        bytes_processed: int,
        bytes_billed: int,
        provider_job_id: str | None,
        duration_ms: int,
    ):
        if type(reservation_id) is not int or reservation_id < 1:
            raise ValueError("Positive reservation id required")

        with repository_transaction() as conn:
            return repository.complete(
                conn,
                reservation_id=reservation_id,
                owner_key=self.owner_key,
                bytes_processed=bytes_processed,
                bytes_billed=bytes_billed,
                provider_job_id=provider_job_id,
                duration_ms=duration_ms,
            )
    def fail(
                self,
                reservation_id: int,
                *,
                bytes_processed: int | None,
                bytes_billed: int | None,
                provider_job_id: str | None,
                duration_ms: int,
        ):
            if type(reservation_id) is not int or reservation_id < 1:
                raise ValueError("Positive reservation id required")

            with repository_transaction() as conn:
                return repository.fail(
                    conn,
                    reservation_id=reservation_id,
                    owner_key=self.owner_key,
                    bytes_processed=bytes_processed,
                    bytes_billed=bytes_billed,
                    provider_job_id=provider_job_id,
                    duration_ms=duration_ms,
                )
    def release(self, reservation_id: int):
        if type(reservation_id) is not int or reservation_id < 1:
            raise ValueError("Positive reservation id required")

        with repository_transaction() as conn:
            return repository.release(
                conn,
                reservation_id=reservation_id,
                owner_key=self.owner_key,
            )
