
import pytest

from google.cloud import bigquery

from tests.bigquery_fake import Client


def test_existing_mumbai_fake_default():
    cloud = Client()

    assert cloud.location == "asia-south1"

    table = bigquery.Table(
        "synthetic-project.dr_dev_w1.test_table"
    )

    created = cloud.create_table(table)

    assert created.location == "asia-south1"


def test_sydney_fake_uses_australian_location():
    cloud = Client(location="australia-southeast1")

    assert cloud.project == "synthetic-project"
    assert cloud.location == "australia-southeast1"

    table = bigquery.Table(
        "synthetic-project.dr_prod_w1.test_table"
    )

    created = cloud.create_table(table)

    assert created.location == "australia-southeast1"


def test_fake_rejects_unsupported_location():
    with pytest.raises(ValueError):
        Client(location="us-central1")
