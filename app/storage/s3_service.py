import boto3


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