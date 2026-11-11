"""
Centralized configuration for storage (S3/AWS) settings.

These values were previously hard-coded independently in
app/storage/s3_service.py, app/processing/silver_processor.py,
app/processing/gold_processor.py, app/api/files.py, and
app/services/processing_service.py. Changing the bucket/region/
profile meant editing every one of those files and hoping none were
missed. They are now read from the environment (with today's values
as defaults, so existing deployments keep working unchanged).
"""

import os
from dotenv import load_dotenv


load_dotenv()

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY"
)

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite",
)

GEMINI_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.7-flash,gemini-3.8-flash",
    ).split(",")
    if model.strip()
]

AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
AWS_PROFILE = os.getenv("AWS_PROFILE", "ai-data-workspace")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "ai-data-workspace-amir-dev")


def get_boto3_session():
    """Local developer profile OR the assigned AWS workload identity.

    Never allow a deployed Lambda/ECS task to look up a developer profile,
    use development defaults, or silently fall back to other credentials.
    The service's actual access is governed by the runtime IAM role.
    """
    import boto3

    lambda_runtime = bool(os.getenv("AWS_EXECUTION_ENV", "").startswith("AWS_Lambda_"))
    ecs_runtime = bool(
        os.getenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
        or os.getenv("AWS_CONTAINER_CREDENTIALS_FULL_URI")
    )
    if lambda_runtime or ecs_runtime:
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        bucket = os.getenv("S3_BUCKET_NAME")
        if (not region or not bucket or region != AWS_REGION
                or bucket != S3_BUCKET_NAME
                or os.getenv("AWS_PROFILE")
                or os.getenv("AWS_SHARED_CREDENTIALS_FILE")
                or os.getenv("AWS_CONFIG_FILE")):
            raise RuntimeError("EXPLICIT_AWS_WORKLOAD_CONFIGURATION_REQUIRED")
        return boto3.Session(region_name=region)
    return boto3.Session(
        profile_name=AWS_PROFILE,
        region_name=AWS_REGION,
    )
