"""Provider-neutral contract for governed analytical reads.

The contract intentionally receives only backend-compiled query objects.
It performs no authorization, SQL compilation, cloud access or persistence.
"""

from dataclasses import dataclass
import re
from typing import Mapping, Protocol, runtime_checkable
from enum import StrEnum
from app.query_governance.types import QueryEstimate


_HEX64 = re.compile(r"^[a-f0-9]{64}$")

# DataRise-generated physical names are deliberately much narrower than the
# complete provider identifier grammar.
_PROJECT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$"
)

_WAREHOUSE_TABLE = re.compile(
    r"^m[1-9][0-9]*"
    r"_v[1-9][0-9]*"
    r"_g[1-9][0-9]*"
    r"(?:"
    r"_p[1-9][0-9]*"
    r"_r[1-9][0-9]*"
    r")?$"
)
MAX_COMPILED_SQL_BYTES = 100_000
MAX_PHYSICAL_TABLES = 32
MAX_EXECUTION_BYTES = 1 << 30
MAX_OUTPUT_ROWS = 1000
MAX_RESULT_PAYLOAD_BYTES = 8 << 20

def _valid_physical_table(
    name: str,
    workspace_id: int,
) -> bool:
    if not isinstance(name, str):
        return False

    parts = name.split(".")

    if len(parts) != 3:
        return False

    project, dataset, table = parts

    return (
        _PROJECT.fullmatch(project) is not None
        and dataset == f"dr_dev_w{workspace_id}"
        and _WAREHOUSE_TABLE.fullmatch(table) is not None
    )
@dataclass(frozen=True)
class CompiledQuery:
    workspace_id: int
    request_token: str
    intent_sha256: str
    sql: str
    physical_tables: tuple[str, ...]
    compiler_version: int = 1

    def __post_init__(self):
        if type(self.workspace_id) is not int or self.workspace_id < 1:
            raise ValueError("Positive workspace id required")

        if not _HEX64.fullmatch(self.request_token):
            raise ValueError("Pinned request token required")

        if not _HEX64.fullmatch(self.intent_sha256):
            raise ValueError("Pinned query intent required")

        if (
            not isinstance(self.sql, str)
            or not self.sql.strip()
            or "\x00" in self.sql
            or len(self.sql.encode("utf-8")) > MAX_COMPILED_SQL_BYTES
        ):
            raise ValueError("Bounded compiled SQL required")

        if type(self.compiler_version) is not int or self.compiler_version not in (1, 2):
            raise ValueError("Unsupported query compiler contract")

        if (
            type(self.physical_tables) is not tuple
            or not 1 <= len(self.physical_tables) <= MAX_PHYSICAL_TABLES
            or len(set(self.physical_tables)) != len(self.physical_tables)
            or any(
                not _valid_physical_table(
                    name,
                    self.workspace_id,
                )
                for name in self.physical_tables
            )
        ):
            raise ValueError("Explicit bounded physical table set required")


@dataclass(frozen=True)
class ExecutionPermit:
    reservation_id: int
    workspace_id: int
    provider: str
    request_token: str
    intent_sha256: str
    reserved_bytes: int
    maximum_bytes_billed: int
    output_rows: int

    def __post_init__(self):
        if type(self.reservation_id) is not int or self.reservation_id < 1:
            raise ValueError("Positive reservation id required")

        if type(self.workspace_id) is not int or self.workspace_id < 1:
            raise ValueError("Positive workspace id required")

        if (
            not isinstance(self.provider, str)
            or not self.provider
            or self.provider != self.provider.upper()
        ):
            raise ValueError("Explicit provider identity required")

        if not _HEX64.fullmatch(self.request_token):
            raise ValueError("Pinned request token required")

        if not _HEX64.fullmatch(self.intent_sha256):
            raise ValueError("Pinned query intent required")
        if (
            type(self.reserved_bytes) is not int
            or not 0 <= self.reserved_bytes <= MAX_EXECUTION_BYTES
        ):
            raise ValueError("Bounded reserved byte budget required")
        if (
            type(self.maximum_bytes_billed) is not int
            or not 1 <= self.maximum_bytes_billed <= MAX_EXECUTION_BYTES
        ):
            raise ValueError("Bounded execution byte permit required")
        if self.reserved_bytes > self.maximum_bytes_billed:
            raise ValueError(
                "Reservation cannot exceed execution byte permit"
            )
        if (
            type(self.output_rows) is not int
            or not 1 <= self.output_rows <= MAX_OUTPUT_ROWS
        ):
            raise ValueError("Bounded output row permit required")

    @classmethod
    def from_claimed_reservation(cls, reservation: Mapping):
        if not isinstance(reservation, Mapping):
            raise ValueError("Claimed durable reservation required")

        if reservation.get("status") != "EXECUTING":
            raise ValueError("Executing durable reservation required")

        try:
            return cls(
                reservation_id=reservation["reservation_id"],
                workspace_id=reservation["workspace_id"],
                provider=reservation["provider"],
                request_token=reservation["request_token"],
                intent_sha256=reservation["intent_sha256"],
                reserved_bytes=reservation["reserved_bytes"],
                maximum_bytes_billed=reservation[
                    "maximum_bytes_billed"
                ],
                output_rows=reservation["output_rows"],
            )
        except KeyError as exc:
            raise ValueError(
                "Incomplete durable execution reservation"
            ) from exc

    def authorize(
        self,
        query: CompiledQuery,
        *,
        provider: str,
    ):
        if not isinstance(query, CompiledQuery):
            raise ValueError("Compiled query required")

        if (
            self.workspace_id != query.workspace_id
            or self.request_token != query.request_token
            or self.intent_sha256 != query.intent_sha256
            or self.provider != provider
        ):
            raise ValueError("Execution permit scope mismatch")


@dataclass(frozen=True)
class QueryExecutionResult:
    provider_job_id: str
    rows: tuple[Mapping[str, object], ...]
    truncated: bool
    bytes_processed: int
    bytes_billed: int
    payload_bytes: int
    duration_ms: int

    def __post_init__(self):
        if (
            not isinstance(self.provider_job_id, str)
            or not 1 <= len(self.provider_job_id) <= 255
        ):
            raise ValueError("Provider job identity required")

        if type(self.rows) is not tuple:
            raise ValueError("Immutable bounded result rows required")

        if len(self.rows) > MAX_OUTPUT_ROWS:
            raise ValueError("Result row ceiling exceeded")

        if any(not isinstance(row, Mapping) for row in self.rows):
            raise ValueError("Result rows must be mappings")

        if type(self.truncated) is not bool:
            raise ValueError("Explicit truncation state required")

        for value in (
            self.bytes_processed,
            self.bytes_billed,
            self.payload_bytes,
            self.duration_ms,
        ):
            if type(value) is not int or value < 0:
                raise ValueError(
                    "Execution telemetry must be non-negative integers"
                )

        if self.payload_bytes > MAX_RESULT_PAYLOAD_BYTES:
            raise ValueError("Result payload ceiling exceeded")

    @property
    def row_count(self) -> int:
        return len(self.rows)

class RecoveryState(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class QueryRecovery:
    state: RecoveryState
    provider_job_id: str
    result: QueryExecutionResult | None = None
    bytes_processed: int | None = None
    bytes_billed: int | None = None
    duration_ms: int = 0

    def __post_init__(self):
        if (
            not isinstance(self.provider_job_id, str)
            or not 1 <= len(self.provider_job_id) <= 255
        ):
            raise ValueError("Provider job identity required")

        for value in (self.bytes_processed, self.bytes_billed):
            if value is not None and (
                type(value) is not int or value < 0
            ):
                raise ValueError("Invalid recovery byte telemetry")

        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise ValueError("Invalid recovery duration")

        if self.state == RecoveryState.SUCCEEDED:
            if self.result is None:
                raise ValueError("Successful recovery requires result")

            if self.result.provider_job_id != self.provider_job_id:
                raise ValueError("Recovered result/job identity mismatch")
        elif self.result is not None:
            raise ValueError(
                "Only successful recovery may contain result"
            )
@runtime_checkable
class ReadQueryProvider(Protocol):
    identity: str
    def estimate(self, query: CompiledQuery) -> QueryEstimate:
        ...

    def execute(
        self,
        query: CompiledQuery,
        permit: ExecutionPermit,
    ) -> QueryExecutionResult:
        ...

    def recover(
        self,
        query: CompiledQuery,
        permit: ExecutionPermit,
    ) -> QueryRecovery:
        ...
