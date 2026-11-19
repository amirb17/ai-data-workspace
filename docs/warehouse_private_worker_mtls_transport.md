# Private warehouse worker RPC (opt-in; no live provider authorization)

This change extends the previously loopback-only RPC to enable a reviewed private service deployment **without changing the legacy loopback mode**.

## Client

Trusted bootstrap creates one `WarehouseWorkerClient` per duty with `host` ending in `.internal`, the service port, a unique HMAC secret and a preloaded certificate-verifying `ssl.SSLContext`. The context must enable server hostname verification and `CERT_REQUIRED`, TLS 1.2 or newer. For genuine mutual TLS, the context must also call `load_cert_chain` with the **API client's** certificate and private key; the code cannot infer that chain is present from `SSLContext` properties, so a real handshake acceptance is mandatory.

Example for trusted bootstrap (NOT configured in production yet):

```python
import ssl
from app.warehouse.worker_rpc_client import WarehouseWorkerClient

ctx = ssl.create_default_context(cafile="/secrets/private-ca.pem")
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
ctx.load_cert_chain("/secrets/api-client.crt", "/secrets/api-client.key")
client = WarehouseWorkerClient(
    duty="accounting", port=18443, secret=private_accounting_hmac_key,
    host="accounting.warehouse.internal", tls_context=ctx,
)
```

Never expose TLS private keys in environment variable values, logs, CLI arguments or the public API.

## Worker

Each duty's **independent ECS task and task IAM role** may start:
```
python -m app.warehouse.worker_rpc_server accounting --port 18443 --private-mtls
```

The container requires private, per-duty `DATARISE_WAREHOUSE_DUTY_DSN`, `DATARISE_WAREHOUSE_RPC_SECRET_B64` and file paths `DATARISE_WAREHOUSE_TLS_CERT`, `DATARISE_WAREHOUSE_TLS_KEY`, `DATARISE_WAREHOUSE_TLS_CLIENT_CA`. These must be provisioned through reviewed, per-task secret mounts. Bind `0.0.0.0` **only in a private subnet** with restrictive inbound security groups (API task SG only), no public IP or internet-facing load balancer, and a private namespace `*.internal`.

The server requires mutual TLS client authentication, validates the HMAC duty/operation signature and preserves the bounded non-replay RPC contract. The provider send operation **continues to reject** `PROVIDER_SEND_BOUNDARY_NOT_APPROVED`. No BigQuery I/O occurs in this transport.

## Mandatory before deployment / enabled execution

- Security review private CA lifecycle, certificate rotation, workload identity and exact per-duty AWS/GCP IAM. The current replay guard is process-memory only: run **one replica per worker duty** until a shared durable replay mechanism is designed.
- Verify actual end-to-end mTLS handshake including no-client-cert, untrusted cert, wrong hostname, wrong duty key and replay rejection. Unit tests only check contract/HTTPS selection, not live PKI.
- Verify the existing PostgreSQL worker roles and ACLs on RDS with no cross-duty role privileges, then perform signed worker health probes.
- Implement cross-process *atomic* authenticated send boundary; **DO NOT** remove the existing provider rejection as part of transport enablement.
- Bind reviewed BigQuery creator/attester/results-reader identities and named-result policy; perform bounded, cost-approved live acceptance.
- Do not change ECS API environment or Terraform in this PR.

These are source-level transport changes only. They do not deploy, configure runtime, authorize BigQuery operations or complete the analyst UI.
