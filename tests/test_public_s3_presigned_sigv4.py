"""Presigned browser uploads use regional SigV4 and explicit CSV type."""
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import boto3
from app.storage import s3_service


def test_public_upload_url_is_regional_sigv4_and_signs_content_type():
    session = boto3.Session(
        aws_access_key_id="ASIATESTKEY000000",
        aws_secret_access_key="test-secret-for-offline-only",
        aws_session_token="test-temporary-token",
        region_name="ap-southeast-2",
    )
    with patch.object(s3_service, "get_boto3_session", return_value=session), patch.object(
        s3_service, "BUCKET_NAME", "datarise-demo-202064056386-ap-southeast-2"
    ):
        url = s3_service.generate_presigned_upload_url(
            "staging/user-1/12345678-1234-1234-1234-123456789012/employees.csv",
            expires_in=900,
        )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "datarise-demo-202064056386-ap-southeast-2.s3.ap-southeast-2.amazonaws.com"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["900"]
    assert "content-type" in query["X-Amz-SignedHeaders"][0].split(";")
    assert "X-Amz-Security-Token" in query
    assert "AWSAccessKeyId" not in query
    assert "Signature" not in query
    assert query["X-Amz-Credential"][0].split("/")[2] == "ap-southeast-2"
