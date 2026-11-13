"""Dedicated dispatcher entrypoint for a separately provisioned EC2 IAM role."""
import logging
import os
import boto3
from app.processing_worker.dispatcher import main, require_dispatch_approval

logger = logging.getLogger(__name__)


def cli():
    try:
        require_dispatch_approval()
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        role = os.getenv("DATARISE_DISPATCH_IAM_ROLE_ARN", "")
        if not region or not role.startswith("arn:aws:iam::") or ":role/" not in role:
            raise PermissionError("Dispatcher IAM role ARN is required")
        session = boto3.Session(region_name=region)
        account, name = role.split("::", 1)[1].split(":role/", 1)
        arn = session.client("sts").get_caller_identity()["Arn"]
        if not arn.startswith(f"arn:aws:sts::{account}:assumed-role/{name}/"):
            raise PermissionError("Dispatcher principal differs from approved instance role")
        return main(session=session)
    except Exception as exc:
        logger.error("Dispatcher refused (%s)", type(exc).__name__)
        return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(cli())
