import boto3
import hashlib

BUCKET_NAME = "ai-data-workspace-amir-dev"
AWS_REGION = "ap-south-1"
AWS_PROFILE = "ai-data-workspace"


def get_s3_client():
    session = boto3.Session(
        profile_name=AWS_PROFILE,
        region_name=AWS_REGION
    )

    return session.client("s3")


def upload_file_to_s3(
    local_file_path: str,
    file_name: str,
    file_hash: str,
):
    s3 = get_s3_client()

    object_key = f"raw/{file_hash}/{file_name}"

    s3.upload_file(
        Filename=local_file_path,
        Bucket=BUCKET_NAME,
        Key=object_key,
    )

    return f"s3://{BUCKET_NAME}/{object_key}"
def generate_presigned_upload_url(
    object_key: str,
    expires_in: int = 900,
):
    s3 = get_s3_client()

    url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": object_key,
        },
        ExpiresIn=expires_in,
    )

    return url

def get_object_metadata(object_key: str):
    s3 = get_s3_client()

    response = s3.head_object(
        Bucket=BUCKET_NAME,
        Key=object_key,
    )

    return {
        "object_key": object_key,
        "size": response["ContentLength"],
        "content_type": response.get("ContentType"),
        "etag": response.get("ETag", "").strip('"'),
    }

def calculate_s3_object_hash(
    object_key: str,
    chunk_size: int = 1024 * 1024,
):
    s3 = get_s3_client()

    response = s3.get_object(
        Bucket=BUCKET_NAME,
        Key=object_key,
    )

    body = response["Body"]

    sha256 = hashlib.sha256()

    while True:
        chunk = body.read(chunk_size)

        if not chunk:
            break

        sha256.update(chunk)

    body.close()

    return sha256.hexdigest()

def promote_staging_object_to_raw(
    staging_object_key: str,
    file_hash: str,
    file_name: str,
):
    s3 = get_s3_client()

    raw_object_key = f"raw/{file_hash}/{file_name}"

    copy_source = {
        "Bucket": BUCKET_NAME,
        "Key": staging_object_key,
    }

    s3.copy_object(
        Bucket=BUCKET_NAME,
        CopySource=copy_source,
        Key=raw_object_key,
    )

    return raw_object_key


def delete_s3_object(object_key: str):
    s3 = get_s3_client()

    s3.delete_object(
        Bucket=BUCKET_NAME,
        Key=object_key,
    )


def build_s3_uri(object_key: str):
    return f"s3://{BUCKET_NAME}/{object_key}"