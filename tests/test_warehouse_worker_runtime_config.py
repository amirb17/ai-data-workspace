"""Fail-closed separate-worker transport configuration tests."""
import base64
import pytest

from app.services.warehouse_worker_runtime_config import configured_worker_router


def environment():
    return {
        "DATARISE_WH_SUBMIT_RPC_PORT": "19081",
        "DATARISE_WH_ATTEST_RPC_PORT": "19082",
        "DATARISE_WH_ACCOUNT_RPC_PORT": "19083",
        "DATARISE_WH_RESULTS_RPC_PORT": "19084",
        "DATARISE_WH_SUBMIT_RPC_SECRET_B64": base64.b64encode(b"a"*32).decode(),
        "DATARISE_WH_ATTEST_RPC_SECRET_B64": base64.b64encode(b"b"*32).decode(),
        "DATARISE_WH_ACCOUNT_RPC_SECRET_B64": base64.b64encode(b"c"*32).decode(),
        "DATARISE_WH_RESULTS_RPC_SECRET_B64": base64.b64encode(b"d"*32).decode(),
    }


def test_worker_router_reads_only_ports_and_signing_keys():
    router=configured_worker_router(environment())
    assert router.submit.port == 19081
    assert router.attest.duty == "recovery_attestation"
    assert router.account.duty == "accounting"
    assert router.results.duty == "result_authorization"


@pytest.mark.parametrize("missing", [
    "DATARISE_WH_SUBMIT_RPC_PORT",
    "DATARISE_WH_RESULTS_RPC_PORT",
    "DATARISE_WH_ATTEST_RPC_SECRET_B64",
    "DATARISE_WH_ACCOUNT_RPC_SECRET_B64",
])
def test_incomplete_worker_configuration_stays_closed(missing):
    variables=environment()
    variables.pop(missing)
    with pytest.raises(ValueError):
        configured_worker_router(variables)


@pytest.mark.parametrize("unsafe", [
    "DATARISE_WAREHOUSE_DUTY_DSN",
    "DATARISE_WAREHOUSE_RPC_SECRET_B64",
])
def test_api_process_cannot_hold_worker_only_credentials(unsafe):
    variables=environment()
    variables[unsafe]="private"
    with pytest.raises(ValueError,match="WORKER_CREDENTIAL_LEAK"):
        configured_worker_router(variables)


def test_ports_and_secrets_must_be_unique():
    variables=environment()
    variables["DATARISE_WH_RESULTS_RPC_PORT"]="19081"
    with pytest.raises(ValueError,match="DISTINCT_WORKER_PORTS"):
        configured_worker_router(variables)
    variables=environment()
    variables["DATARISE_WH_RESULTS_RPC_SECRET_B64"]=variables["DATARISE_WH_SUBMIT_RPC_SECRET_B64"]
    with pytest.raises(ValueError,match="DISTINCT_WORKER_RPC_SECRETS"):
        configured_worker_router(variables)


def test_rejects_invalid_base64_and_weak_key():
    variables=environment()
    variables["DATARISE_WH_SUBMIT_RPC_SECRET_B64"]="invalid-b64!"
    with pytest.raises(ValueError,match="SECRET_INVALID"):
        configured_worker_router(variables)
    variables["DATARISE_WH_SUBMIT_RPC_SECRET_B64"]=base64.b64encode(b"weak").decode()
    with pytest.raises(ValueError,match="TOO_SHORT"):
        configured_worker_router(variables)
