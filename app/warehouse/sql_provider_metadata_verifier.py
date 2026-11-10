"""Step 410A: independent, metadata-only BigQuery serving-table verification.

Never run SQL. The caller must supply internal publication-pinned bindings
resolved under current workspace ownership and complete expected physical
schemas derived from approved model contexts. This checks live table metadata,
not row contents, publication freshness, or query execution authorization.
"""
from dataclasses import dataclass
from typing import Protocol
from google.api_core import exceptions as google_errors
from google.auth.exceptions import DefaultCredentialsError, RefreshError

from app.warehouse.providers.base import Binding, ErrorCode, ProviderError
from app.warehouse.sql_physical_binding import InternalDraftBinding


class TableMetadataClient(Protocol):
    def get_table(self, table_name: str, **kwargs): ...


@dataclass(frozen=True)
class MetadataAttestation:
    model_id: int
    model_version_id: int
    resource_id: int
    publication_id: int
    publication_revision: int
    table_metadata_verified: bool = True
    row_contents_verified: bool = False
    query_execution_authorized: bool = False


def verify_serving_table_metadata(
    binding: InternalDraftBinding, *,
    workspace_id: int,
    expected_schemas: dict[int, tuple[tuple[str, str, str], ...]],
    client: TableMetadataClient,
    project: str,
    location: str = "asia-south1",
) -> tuple[MetadataAttestation, ...]:
    """Call only get_table for exact backend-derived physical publication tables.

    A receipt (or caller-supplied expected schema) alone is not verification.
    No execution flag is returned. Any uncertainty fails closed.
    """
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("Positive workspace ID required")
    if type(binding) is not InternalDraftBinding or binding.executable is not False:
        raise ValueError("Internal non-executable publication binding required")
    if not 1 <= len(binding.models) <= 2:
        raise ValueError("One or two uniquely pinned warehouse models required")
    if not isinstance(expected_schemas, dict) or set(expected_schemas) != {
        model.model_id for model in binding.models
    }:
        raise ValueError("Complete expected schemas required for each bound model")
    if client is None or getattr(client, "project", None) != project:
        raise ProviderError(ErrorCode.PERMISSION_DENIED)

    attestations=[]
    seen=set()
    for model in binding.models:
        if model.model_id in seen:
            raise ValueError("Duplicate model binding")
        seen.add(model.model_id)
        generated=Binding(
            project,workspace_id,model.model_id,model.model_version_id,
            model.resource_id,model.generation,location=location,
        )
        expected_table=generated.publication_physical(
            model.publication_id,model.publication_revision
        )
        if model.physical_table != expected_table:
            raise ProviderError(ErrorCode.PERMISSION_DENIED)
        schema=expected_schemas[model.model_id]
        if (type(schema) is not tuple or not schema or
                any(type(field) is not tuple or len(field)!=3 or
                    not all(isinstance(value,str) and value for value in field)
                    for field in schema)):
            raise ValueError("Complete typed physical schema required")
        try:
            found=client.get_table(expected_table,retry=None,timeout=10)
        except Exception as error:
            # The read cannot be trusted. Preserve provider error categories,
            # but never disclose exception details or resource identifiers.
            reasons={
                item.get("reason") for item in (getattr(error,"errors",None) or ())
                if isinstance(item,dict)
            }
            if isinstance(error,(DefaultCredentialsError,RefreshError,
                                 google_errors.Unauthorized)):
                code=ErrorCode.AUTHENTICATION_FAILED
            elif reasons & {"rateLimitExceeded","jobRateLimitExceeded"} or isinstance(
                    error,google_errors.TooManyRequests):
                code=ErrorCode.RATE_LIMITED
            elif reasons & {"quotaExceeded","billingTierLimitExceeded"}:
                code=ErrorCode.QUOTA_EXCEEDED
            elif reasons & {"billingNotEnabled","accessDenied"} or isinstance(
                    error,google_errors.Forbidden):
                code=ErrorCode.PERMISSION_DENIED
            elif isinstance(error,google_errors.NotFound):
                code=ErrorCode.RESOURCE_NOT_FOUND
            elif isinstance(error,(google_errors.ServerError,TimeoutError,ConnectionError)):
                code=ErrorCode.RETRYABLE_PROVIDER_ERROR
            else:
                code=ErrorCode.PERMANENT_PROVIDER_ERROR
            raise ProviderError(code,uncertain=True) from None

        try:
            actual_schema=tuple(
                (str(field.name),str(field.field_type),str(field.mode))
                for field in found.schema
            )
        except (AttributeError,TypeError,ValueError):
            # A broken SDK response is not verified metadata.
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH,uncertain=True) from None
        expected_schema=tuple(
            (name,{"INTEGER":"INT64","BOOLEAN":"BOOL"}.get(kind,kind),mode)
            for name,kind,mode in schema
        )
        actual_schema=tuple(
            (name,{"INTEGER":"INT64","BOOLEAN":"BOOL"}.get(kind,kind),mode)
            for name,kind,mode in actual_schema
        )
        labels={"datarise":"warehouse","environment":"development",
                "workspace":str(workspace_id),"model":str(model.model_id),
                "generation":str(model.generation),
                # The provider writes the approved physical schema hash as
                # a 63-character BigQuery label; it is NOT a serving-state hash.
                "contract":model.schema_sha256[:63],
                "publication":str(model.publication_id),
                "revision":str(model.publication_revision)}
        if (getattr(found,"full_table_id",None) != expected_table.replace(".",":",1)
                or getattr(found,"table_type",None) != "TABLE"
                or getattr(found,"location",None) != location
                or actual_schema != expected_schema
                or getattr(found,"time_partitioning",None) is not None
                or getattr(found,"range_partitioning",None) is not None
                or getattr(found,"clustering_fields",None) is not None
                or bool(getattr(found,"require_partition_filter",False))
                or getattr(found,"expires",None) is not None
                or not isinstance(getattr(found,"labels",None),dict)
                or any(found.labels.get(k)!=v for k,v in labels.items())):
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        attestations.append(MetadataAttestation(
            model.model_id,model.model_version_id,model.resource_id,
            model.publication_id,model.publication_revision,
        ))
    return tuple(attestations)
