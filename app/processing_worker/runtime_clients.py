"""Bounded SDK clients for the serial runtime; construction is caller-owned."""
from botocore.config import Config


def client_config(service: str) -> Config:
    read_timeout = {"sqs": 15, "s3": 20, "secretsmanager": 10, "lambda": 45}[service]
    return Config(
        connect_timeout=3, read_timeout=read_timeout, max_pool_connections=2,
        # A synchronous invoke may have executed despite a lost response. Let
        # durable job recovery decide the next attempt, not an invisible retry.
        retries={"mode": "standard", "total_max_attempts": 1 if service == "lambda" else 2},
        tcp_keepalive=True,
    )
