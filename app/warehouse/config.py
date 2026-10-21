"""Opt-in metadata settings, following the application's APP_ENV convention."""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class WarehouseSettings:
    enabled: bool
    provider: str = 'BIGQUERY'
    environment: str = 'development'
    production_location: str = 'asia-south1'


def settings() -> WarehouseSettings:
    enabled = os.getenv('WAREHOUSE_ENABLED', 'false').lower()
    if enabled not in ('true', 'false'):
        raise ValueError('WAREHOUSE_ENABLED must be true or false')
    # Disabled installations require no provider, environment or credentials.
    if enabled == 'false':
        return WarehouseSettings(False)
    provider = os.getenv('WAREHOUSE_PROVIDER', 'bigquery').upper()
    environment = os.getenv('APP_ENV', 'production')
    if provider != 'BIGQUERY' or environment not in ('development', 'production'):
        raise ValueError('Unsupported warehouse provider or application environment')
    return WarehouseSettings(True, provider, environment)


def require_enabled() -> WarehouseSettings:
    value = settings()
    if not value.enabled:
        raise RuntimeError('Warehouse metadata is disabled')
    return value
