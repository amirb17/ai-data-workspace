import pytest

from app.services import file_service


def test_validate_upload_context_requires_both_or_neither():
    # Neither provided -> fine.
    file_service.validate_upload_context(
        user_id=1, workspace_id=None, dataset_id=None
    )

    # Only one provided -> rejected before any DB lookup.
    with pytest.raises(ValueError):
        file_service.validate_upload_context(
            user_id=1, workspace_id=1, dataset_id=None
        )

    with pytest.raises(ValueError):
        file_service.validate_upload_context(
            user_id=1, workspace_id=None, dataset_id=1
        )


def test_validate_upload_context_rejects_mismatched_dataset(monkeypatch):
    monkeypatch.setattr(
        file_service, "get_dataset_by_workspace", lambda **kwargs: None
    )

    with pytest.raises(ValueError, match="does not belong to workspace"):
        file_service.validate_upload_context(
            user_id=1, workspace_id=1, dataset_id=999
        )


def test_validate_upload_context_accepts_matching_dataset(monkeypatch):
    monkeypatch.setattr(
        file_service,
        "get_dataset_by_workspace",
        lambda **kwargs: (1, "name", None, None, "ACTIVE", None, None, 1),
    )

    # Should not raise.
    file_service.validate_upload_context(
        user_id=1, workspace_id=1, dataset_id=1
    )


def test_register_file_duplicate_still_creates_upload_request(monkeypatch):
    """
    Regression test: register_file used to only call
    create_upload_request inside the `else` (new file) branch, so a
    duplicate upload raised UnboundLocalError instead of returning a
    result.
    """

    existing_file = (1, "a.csv", 10, "hash", "s3://bucket/raw/hash/a.csv", "UPLOADED")

    monkeypatch.setattr(
        file_service, "find_physical_file_by_hash", lambda file_hash: existing_file
    )

    created_upload_requests = []

    def fake_create_upload_request(**kwargs):
        created_upload_requests.append(kwargs)
        return (99, kwargs["user_id"], kwargs["file_id"], None, None, "UPLOADED", None)

    monkeypatch.setattr(
        file_service, "create_upload_request", fake_create_upload_request
    )

    result = file_service.register_file(
        user_id=1,
        file_name="a.csv",
        file_size=10,
        file_hash="hash",
        local_file_path="/tmp/a.csv",
    )

    assert result["is_duplicate"] is True
    assert result["upload_request"] is not None
    assert len(created_upload_requests) == 1


def test_finalize_presigned_upload_rejects_foreign_object_key(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("should not reach S3 for a rejected object_key")

    monkeypatch.setattr(file_service, "get_object_metadata", fail_if_called)

    with pytest.raises(ValueError, match="own staging upload"):
        file_service.finalize_presigned_upload(
            user_id=1,
            object_key="staging/user-2/some-token/other.csv",
            expected_file_size=10,
        )


def test_finalize_presigned_upload_rejects_path_traversal(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("should not reach S3 for a rejected object_key")

    monkeypatch.setattr(file_service, "get_object_metadata", fail_if_called)

    with pytest.raises(ValueError, match="own staging upload"):
        file_service.finalize_presigned_upload(
            user_id=1,
            object_key="staging/user-1/../user-2/token/other.csv",
            expected_file_size=10,
        )


def test_finalize_presigned_upload_treats_lost_insert_race_as_duplicate(monkeypatch):
    """
    Two concurrent completions of identical bytes can both miss the
    find_physical_file_by_hash check and race on the unique file_hash
    constraint. create_uploaded_physical_file resolves this with
    ON CONFLICT DO NOTHING + fallback select and reports
    was_inserted=False; finalize_presigned_upload must surface this as
    a duplicate instead of silently reporting a fresh upload.
    """

    monkeypatch.setattr(
        file_service, "get_object_metadata", lambda object_key: {"size": 10}
    )
    monkeypatch.setattr(
        file_service, "calculate_s3_object_hash", lambda object_key: "hash"
    )
    monkeypatch.setattr(
        file_service, "find_physical_file_by_hash", lambda file_hash: None
    )
    monkeypatch.setattr(
        file_service,
        "promote_staging_object_to_raw",
        lambda **kwargs: "raw/hash/a.csv",
    )
    monkeypatch.setattr(file_service, "build_s3_uri", lambda key: f"s3://bucket/{key}")
    monkeypatch.setattr(file_service, "delete_s3_object", lambda object_key: None)

    winning_file = (1, "a.csv", 10, "hash", "s3://bucket/raw/hash/a.csv", "UPLOADED")

    monkeypatch.setattr(
        file_service,
        "create_uploaded_physical_file",
        lambda **kwargs: (winning_file, False),
    )
    monkeypatch.setattr(
        file_service,
        "create_upload_request",
        lambda **kwargs: (1, kwargs["user_id"], kwargs["file_id"], None, None, "UPLOADED", None),
    )

    result = file_service.finalize_presigned_upload(
        user_id=1,
        object_key="staging/user-1/token/a.csv",
        expected_file_size=10,
    )

    assert result["is_duplicate"] is True
