"""Planned provider identity only; intentionally no execution interface in 11A."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class WarehouseProvider:
    identity: Literal['BIGQUERY'] = 'BIGQUERY'
    supports_execution: bool = False


PLANNED_PROVIDER = WarehouseProvider()
