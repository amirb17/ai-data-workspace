"""Deterministic identity for publication -> serving materialization.

No database access, provider access or cloud credentials.
"""

import re

from app.warehouse.accepted_changes import (
    digest,
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
            "REPLACE_SERVING_TABLE",
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