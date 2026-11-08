
"""Bounded Sydney BigQuery provider policy.

Pure configuration only. Constructing this policy does
not authorize production activation or cloud access.
"""

from dataclasses import dataclass

from app.warehouse.providers.base import (
    ErrorCode,
    ProviderError,
)


@dataclass(frozen=True)
class SydneyPolicy:
    project: str
    enabled: bool = False
    location: str = "australia-southeast1"
    maximum_bytes: int = 1 << 30
    timeout_seconds: int = 60
    maximum_rows: int = 1000
    maximum_payload_bytes: int = 8 << 20

    def __post_init__(self):
        import re

        if not re.fullmatch(
            r"[a-z][a-z0-9-]{4,28}[a-z0-9]",
            self.project,
        ):
            raise ProviderError(
                ErrorCode.RESOURCE_NOT_FOUND
            )

        if self.location != "australia-southeast1":
            raise ProviderError(
                ErrorCode.LOCATION_MISMATCH
            )

        if (
            type(self.enabled) is not bool
            or not 1 <= self.maximum_bytes <= 1 << 30
            or not 1 <= self.timeout_seconds <= 300
            or not 1 <= self.maximum_rows <= 1000
            or not 1 <= self.maximum_payload_bytes <= 8 << 20
        ):
            raise ValueError(
                "Invalid bounded Sydney provider policy"
            )
