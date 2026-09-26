from fastapi.testclient import TestClient

from app.main import app
from app.api import files as files_api


client = TestClient(app)


def test_upload_endpoint_passes_workspace_and_dataset_ids(monkeypatch):
    """
    Regression test: /files/upload previously referenced an undefined
    `request` object (leftover from an `import urllib.request`) when
    forwarding workspace_id/dataset_id, so every direct upload raised
    an AttributeError before this fix.
    """

    captured = {}

    def fake_register_file(**kwargs):
        captured.update(kwargs)
        return {
            "is_duplicate": False,
            "file": (1, "a.csv", 4, "hash", "s3://bucket/raw/hash/a.csv", "UPLOADED"),
            "upload_request": (1, kwargs["user_id"], 1, kwargs["workspace_id"], kwargs["dataset_id"], "UPLOADED", None),
        }

    monkeypatch.setattr(files_api, "register_file", fake_register_file)

    response = client.post(
        "/files/upload",
        data={"user_id": 1, "workspace_id": 5, "dataset_id": 7},
        files={"file": ("a.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 200
    assert captured["workspace_id"] == 5
    assert captured["dataset_id"] == 7

    body = response.json()
    assert body["upload_request"]["workspace_id"] == 5
    assert body["upload_request"]["dataset_id"] == 7


def test_upload_endpoint_rejects_non_csv():
    response = client.post(
        "/files/upload",
        data={"user_id": 1},
        files={"file": ("a.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 400


def test_upload_endpoint_maps_value_error_to_400(monkeypatch):
    def fake_register_file(**kwargs):
        raise ValueError("Dataset 7 does not belong to workspace 5")

    monkeypatch.setattr(files_api, "register_file", fake_register_file)

    response = client.post(
        "/files/upload",
        data={"user_id": 1, "workspace_id": 5, "dataset_id": 7},
        files={"file": ("a.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 400
