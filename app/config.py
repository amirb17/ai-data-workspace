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

AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
AWS_PROFILE = os.getenv("AWS_PROFILE", "ai-data-workspace")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "ai-data-workspace-amir-dev")


def get_boto3_session():
    import boto3

    return boto3.Session(
        profile_name=AWS_PROFILE,
        region_name=AWS_REGION,
    )
