"""Deterministic identity for publication -> serving materialization.

No database access, provider access or cloud credentials.
"""

import re

from app.warehouse.accepted_changes import (
    digest,
)
from dataclasses import dataclass

from app.warehouse.fact_planning import (
    FactContext,
)
from app.warehouse.providers import (
    model_adapter,
)
from app.warehouse.providers.base import (
    Binding,
    ErrorCode,
    ProviderError,
)
from app.warehouse.scd_types import (
    ModelContext,
)

_HEX64 = re.compile(
    r"^[a-f0-9]{64}$"
)

MATERIALIZATION_VERSION = 1


def materialization_identity(
    *,
    workspace_id: int,
    model_id: int,
    model_version_id: int,
    resource_id: int,
    generation: int,
    publication_id: int,
    publication_revision: int,
    physical_table: str,
    configuration_sha256: str,
    schema_sha256: str,
    source_vector_sha256: str,
):
    positive = (
        workspace_id,
        model_id,
        model_version_id,
        resource_id,
        generation,
        publication_id,
        publication_revision,
    )

    if any(
        type(value) is not int
        or value < 1
        for value in positive
    ):
        raise ValueError(
            "Positive serving materialization pins required"
        )

    if any(
        not isinstance(value, str)
        or not _HEX64.fullmatch(value)
        for value in (
            configuration_sha256,
            schema_sha256,
            source_vector_sha256,
        )
    ):
        raise ValueError(
            "Pinned serving contract hashes required"
        )

    if (
        not isinstance(
            physical_table,
            str,
        )
        or not physical_table
        or "\x00" in physical_table
        or len(physical_table) > 2048
    ):
        raise ValueError(
            "Bounded serving table required"
        )

    materialization_token = digest(
        [
            "datarise-serving-materialization",
            MATERIALIZATION_VERSION,
            workspace_id,
            model_id,
            model_version_id,
            resource_id,
            generation,
            publication_id,
            publication_revision,
            configuration_sha256,
            schema_sha256,
            source_vector_sha256,
        ]
    )

    intent_sha256 = digest(
        [
            "datarise-serving-materialization-intent",
            MATERIALIZATION_VERSION,
            materialization_token,
            physical_table,
            "WRITE_EMPTY_PUBLICATION_TABLE",
        ]
    )

    return {
        "materialization_token": (
            materialization_token
        ),
        "intent_sha256": (
            intent_sha256
        ),
    }
@dataclass(frozen=True)
class ServingMaterializationRequest:
    """Complete immutable current-state request for one published head."""

    binding: Binding

    publication_id: int
    publication_revision: int

    materialization_token: str
    intent_sha256: str

    configuration_sha256: str
    schema_sha256: str
    source_vector_sha256: str

    physical_table: str

    context: (
        FactContext
        | ModelContext
    )

    states: tuple

    def __post_init__(
        self,
    ):
        if not isinstance(
            self.binding,
            Binding,
        ):
            raise ValueError(
                "Warehouse binding required"
            )

        if (
            type(
                self.publication_id
            )
            is not int
            or self.publication_id < 1
            or type(
                self.publication_revision
            )
            is not int
            or self.publication_revision < 1
        ):
            raise ValueError(
                "Positive publication pins required"
            )

        if (
            not isinstance(
                self.context,
                (
                    FactContext,
                    ModelContext,
                ),
            )
            or (
                self.context.model_id,
                self.context.model_version_id,
            )
            != (
                self.binding.model_id,
                self.binding.version_id,
            )
        ):
            raise ProviderError(
                ErrorCode.SCHEMA_MISMATCH
            )

        if not isinstance(
            self.states,
            tuple,
        ):
            raise ValueError(
                "Immutable serving state required"
            )

        hashes = (
            self.materialization_token,
            self.intent_sha256,
            self.configuration_sha256,
            self.schema_sha256,
            self.source_vector_sha256,
        )

        if any(
            not isinstance(
                value,
                str,
            )
            or not _HEX64.fullmatch(
                value
            )
            for value in hashes
        ):
            raise ValueError(
                "Pinned serving hashes required"
            )

        expected_table = (
            self.binding
            .publication_physical(
                self.publication_id,
                self.publication_revision,
            )
        )

        if (
            self.physical_table
            != expected_table
        ):
            raise ProviderError(
                ErrorCode.PERMISSION_DENIED
            )

        if (
            self.configuration_sha256
            != configuration_digest(
                self.context
            )
        ):
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            )

        if (
            self.schema_sha256
            != model_adapter.schema_hash(
                self.context
            )
        ):
            raise ProviderError(
                ErrorCode.SCHEMA_MISMATCH
            )

        expected_identity = (
            materialization_identity(
                workspace_id=(
                    self.binding.workspace_id
                ),
                model_id=(
                    self.binding.model_id
                ),
                model_version_id=(
                    self.binding.version_id
                ),
                resource_id=(
                    self.binding.resource_id
                ),
                generation=(
                    self.binding.generation
                ),
                publication_id=(
                    self.publication_id
                ),
                publication_revision=(
                    self.publication_revision
                ),
                physical_table=(
                    self.physical_table
                ),
                configuration_sha256=(
                    self.configuration_sha256
                ),
                schema_sha256=(
                    self.schema_sha256
                ),
                source_vector_sha256=(
                    self.source_vector_sha256
                ),
            )
        )

        if (
            self.materialization_token
            != expected_identity[
                "materialization_token"
            ]
            or self.intent_sha256
            != expected_identity[
                "intent_sha256"
            ]
        ):
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            )

        physical_rows = (
            model_adapter
            .rows_from_states(
                self.context,
                self.states,
            )
        )

        # Encoding and independent physical decoding must agree exactly
        # before any cloud mutation can be attempted.
        if (
            model_adapter.states(
                self.context,
                physical_rows,
            )
            != self.states
        ):
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            )

    @property
    def physical_rows(
        self,
    ):
        return tuple(
            model_adapter
            .rows_from_states(
                self.context,
                self.states,
            )
        )

    @property
    def row_count(
        self,
    ):
        return len(
            self.physical_rows
        )

    @property
    def serving_sha256(
        self,
    ):
        return digest(
            [
                "datarise-serving-state",
                MATERIALIZATION_VERSION,
                self.schema_sha256,
                self.physical_rows,
            ]
        )
@dataclass(frozen=True)
class ServingMaterializationResult:
    """Verified physical outcome of one serving-table materialization."""

    publication_id: int
    publication_revision: int
    generation: int

    materialization_token: str
    intent_sha256: str

    provider_job_id: str

    serving_sha256: str
    row_count: int

    bytes_processed: int
    bytes_billed: int

    def __post_init__(self):
        if any(
            type(value) is not int
            or value < 1
            for value in (
                self.publication_id,
                self.publication_revision,
                self.generation,
            )
        ):
            raise ValueError(
                "Positive serving result pins required"
            )

        if any(
            not isinstance(value, str)
            or not _HEX64.fullmatch(value)
            for value in (
                self.materialization_token,
                self.intent_sha256,
                self.serving_sha256,
            )
        ):
            raise ValueError(
                "Pinned serving result hashes required"
            )

        if (
            not isinstance(
                self.provider_job_id,
                str,
            )
            or not 1
            <= len(
                self.provider_job_id
            )
            <= 255
        ):
            raise ValueError(
                "Bounded provider job identity required"
            )

        if (
            type(self.row_count) is not int
            or self.row_count < 0
            or type(self.bytes_processed) is not int
            or self.bytes_processed < 0
            or type(self.bytes_billed) is not int
            or self.bytes_billed < 0
        ):
            raise ValueError(
                "Valid serving result accounting required"
            )
def configuration_digest(
    context,
):
    if isinstance(
        context,
        FactContext,
    ):
        if (
            context.configuration_sha256
            is not None
        ):
            return (
                context
                .configuration_sha256
            )

    if not isinstance(
        context,
        (
            FactContext,
            ModelContext,
        ),
    ):
        raise ValueError(
            "Typed warehouse context required"
        )

    return digest(
        context
        .configuration
        .model_dump(
            mode="json"
        )
    )
