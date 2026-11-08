
import json
from urllib.parse import urlsplit, unquote

import pytest

from app.processing_worker.runtime_db_secret import database_url_from_secret


def make_secret(**overrides):
    data = {
        "username": "datarise_worker",
        "password": "example-password",
        "host": "db.example.amazonaws.com",
        "port": 5432,
        "dbname": "aiworkspace",
    }
    data.update(overrides)
    return json.dumps(data)


def test_valid_worker_secret():
    url = database_url_from_secret(
        make_secret(), "datarise_worker"
    )

    parsed = urlsplit(url)

    assert parsed.scheme == "postgresql"
    assert parsed.username == "datarise_worker"
    assert parsed.password == "example-password"
    assert parsed.hostname == "db.example.amazonaws.com"
    assert parsed.port == 5432
    assert parsed.path == "/aiworkspace"


def test_special_characters_in_password():
    password = "p@ss:/?#% word"

    url = database_url_from_secret(
        make_secret(password=password),
        "datarise_worker",
    )

    assert unquote(urlsplit(url).password) == password


def test_worker_rejects_verifier_identity():
    with pytest.raises(ValueError, match="role mismatch"):
        database_url_from_secret(
            make_secret(username="datarise_verifier"),
            "datarise_worker",
        )


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "not-json",
        "[]",
        "{}",
        make_secret(password=""),
        make_secret(port=0),
        make_secret(port=True),
        make_secret(host="localhost/path"),
        make_secret(host=""),
    ],
)
def test_invalid_secrets_are_rejected(secret):
    with pytest.raises(ValueError):
        database_url_from_secret(secret, "datarise_worker")


def test_missing_required_field():
    data = json.loads(make_secret())
    del data["dbname"]

    with pytest.raises(ValueError, match="missing required fields"):
        database_url_from_secret(
            json.dumps(data),
            "datarise_worker",
        )
