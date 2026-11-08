
"""Sydney registration authorization in disposable PostgreSQL schemas."""

from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb
from app.warehouse.sydney_binding import Approval, Registry
from app.services.sydney_warehouse_resource_service import (
    resolve_registered_sydney_resource,
)
from tests.test_warehouse_foundation import pg, approved
from tests.test_warehouse_modeling import (
    modeling_pg,
    configured,
    dimension,
)

from app.services import warehouse_model_service as models

MIGRATION = Path("migrations/0046_sydney_warehouse_approval.sql")


@pytest.fixture
def sydney_pg(pg):
    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))

    model_id, version_id = approved(pg)

    return pg, model_id, version_id


def create_approval(conn, workspace_id):
    return conn.execute(
        """
        INSERT INTO warehouse_sydney_approvals (
            workspace_id,
            project,
            loader_identity,
            query_identity,
            approval_revision
        )
        VALUES (%s, %s, %s, %s, 1)
        RETURNING approval_id
        """,
        (
            workspace_id,
            "synthetic-project",
            "synthetic-loader@synthetic-project.iam.gserviceaccount.com",
            "synthetic-query@synthetic-project.iam.gserviceaccount.com",
        ),
    ).fetchone()[0]


def insert_resource(
    conn,
    pg,
    model_id,
    version_id,
    approval_id,
    *,
    environment="production",
    location="australia-southeast1",
    project="synthetic-project",
    dataset=None,
    table=None,
):
    workspace_id = pg["workspace"]

    reference = {
        "reference_version": 1,
        "project": project,
        "dataset": (
            f"dr_prod_w{workspace_id}"
            if dataset is None else dataset
        ),
        "table": (
            f"m{model_id}_v{version_id}_g1"
            if table is None else table
        ),
    }

    return conn.execute(
        """
        INSERT INTO warehouse_resources (
            workspace_id,
            warehouse_model_id,
            warehouse_model_version_id,
            provider,
            environment,
            location,
            generation,
            physical_reference,
            sydney_approval_id,
            created_by
        )
        VALUES (
            %s, %s, %s, 'BIGQUERY',
            %s, %s, 1, %s, %s, %s
        )
        RETURNING resource_id
        """,
        (
            workspace_id,
            model_id,
            version_id,
            environment,
            location,
            Jsonb(reference),
            approval_id,
            pg["owner"]["user_id"],
        ),
    ).fetchone()[0]


def test_approved_sydney_resource_registered(sydney_pg):
    p, model_id, version_id = sydney_pg

    with p["connect"]() as conn:
        approval_id = create_approval(conn, p["workspace"])

        resource_id = insert_resource(
            conn, p, model_id, version_id, approval_id
        )

        row = conn.execute(
            """
            SELECT environment, location, sydney_approval_id
            FROM warehouse_resources
            WHERE resource_id = %s
            """,
            (resource_id,),
        ).fetchone()

    assert row == (
        "production",
        "australia-southeast1",
        approval_id,
    )


def test_missing_sydney_approval_rejected(sydney_pg):
    p, model_id, version_id = sydney_pg

    with pytest.raises(psycopg.errors.RaiseException):
        with p["connect"]() as conn:
            insert_resource(
                conn, p, model_id, version_id, None
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"project": "different-project"},
        {"dataset": "dr_prod_wrong"},
        {"table": "incorrect_table"},
    ],
)
def test_sydney_physical_identity_mismatch_rejected(
    sydney_pg, overrides
):
    p, model_id, version_id = sydney_pg

    with p["connect"]() as conn:
        approval_id = create_approval(conn, p["workspace"])

    with pytest.raises(psycopg.errors.RaiseException):
        with p["connect"]() as conn:
            insert_resource(
                conn,
                p,
                model_id,
                version_id,
                approval_id,
                **overrides,
            )


def test_non_sydney_resource_cannot_use_sydney_approval(
    sydney_pg,
):
    p, model_id, version_id = sydney_pg

    with p["connect"]() as conn:
        approval_id = create_approval(conn, p["workspace"])

    with pytest.raises(psycopg.errors.RaiseException):
        with p["connect"]() as conn:
            insert_resource(
                conn,
                p,
                model_id,
                version_id,
                approval_id,
                environment="development",
                location="asia-south1",
            )


def test_sydney_resource_identity_cannot_change(sydney_pg):
    p, model_id, version_id = sydney_pg

    with p["connect"]() as conn:
        approval_id = create_approval(conn, p["workspace"])
        resource_id = insert_resource(
            conn, p, model_id, version_id, approval_id
        )

    with pytest.raises(psycopg.errors.RaiseException):
        with p["connect"]() as conn:
            conn.execute(
                """
                UPDATE warehouse_resources
                SET generation = 2
                WHERE resource_id = %s
                """,
                (resource_id,),
            )


def test_existing_mumbai_registration_remains_allowed(
    sydney_pg,
):
    p, model_id, version_id = sydney_pg

    with p["connect"]() as conn:
        resource_id = insert_resource(
            conn,
            p,
            model_id,
            version_id,
            None,
            location="asia-south1",
        )

    assert resource_id > 0

def test_sydney_approval_from_another_workspace_rejected(
    sydney_pg,
):
    p, model_id, version_id = sydney_pg

    # Create another workspace using the existing authorized
    # PostgreSQL fixture's owner, without modifying public data.
    with p["connect"]() as conn:
        other_workspace = conn.execute(
        """
        INSERT INTO workspaces (
            workspace_name,
            owner
        )
        VALUES (%s, %s)
        RETURNING workspace_id
        """,
        (
            "Synthetic Sydney Isolation Workspace",
            p["owner"]["owner_key"],
        ),
    ).fetchone()[0]

        other_approval_id = create_approval(
            conn,
            other_workspace,
        )

    with pytest.raises(
        (
            psycopg.errors.ForeignKeyViolation,
            psycopg.errors.RaiseException,
        )
    ):
        with p["connect"]() as conn:
            insert_resource(
                conn,
                p,
                model_id,
                version_id,
                other_approval_id,
            )

def test_registered_sydney_resource_resolves_with_persisted_approval(
    sydney_modeling_pg,
    monkeypatch,
):
    p, model_id, version_id = sydney_modeling_pg
    workspace_id = p["workspace"]

    # 1. Create synthetic approval and registered Sydney resource.
    with p["connect"]() as conn:
        approval_id = create_approval(conn, workspace_id)

        resource_id = insert_resource(
            conn,
            p,
            model_id,
            version_id,
            approval_id,
        )

    # 2. Build the independently reviewed synthetic registry.
    approved_binding = Approval(
        project="synthetic-project",
        workspace_id=workspace_id,
        loader_identity=(
            "synthetic-loader@synthetic-project.iam.gserviceaccount.com"
        ),
        query_identity=(
            "synthetic-query@synthetic-project.iam.gserviceaccount.com"
        ),
        revision=1,
    )

    registry = Registry([approved_binding])

    # 3. Enable only the local production-context validation.
    # This does not activate BigQuery or any cloud integration.
    monkeypatch.setenv("WAREHOUSE_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")

    # 4. Resolve the real PostgreSQL resource and approval.
    result = resolve_registered_sydney_resource(
        workspace_id,
        model_id,
        version_id,
        resource_id,
        p["owner"],
        registry=registry,
        expected_approval=approved_binding.fingerprint,
        publication_id=5,
        revision=6,
        schema_sha256="a" * 64,
        source_fingerprint="b" * 64,
    )

    # 5. Verify the exact persisted identity and destination.
    assert result["sydney_approval_id"] == approval_id
    assert result["approval_revision"] == 1
    assert result["approval_sha256"] == approved_binding.fingerprint

    assert result["location"] == "australia-southeast1"
    assert result["project"] == "synthetic-project"
    assert result["dataset"] == f"dr_prod_w{workspace_id}"

    assert result["physical_table"] == (
        f"synthetic-project.dr_prod_w{workspace_id}."
        f"m{model_id}_v{version_id}_g1_p5_r6"
    )
@pytest.fixture
def sydney_modeling_pg(modeling_pg):
    p = modeling_pg

    model_id, version_id, _, _ = configured(
        p,
        dimension(),
    )

    models.approve_version(
        p["workspace"],
        model_id,
        version_id,
        p["owner"],
    )

    with p["connect"]() as conn:
        conn.execute(
            MIGRATION.read_text(encoding="utf-8")
        )

    return p, model_id, version_id

def test_sydney_persisted_approval_revision_drift_rejected(
    sydney_modeling_pg,
    monkeypatch,
):
    p, model_id, version_id = sydney_modeling_pg
    workspace_id = p["workspace"]

    with p["connect"]() as conn:
        # Database approval is revision 2.
        approval_id = conn.execute(
            """
            INSERT INTO warehouse_sydney_approvals (
                workspace_id,
                project,
                loader_identity,
                query_identity,
                approval_revision
            )
            VALUES (%s, %s, %s, %s, 2)
            RETURNING approval_id
            """,
            (
                workspace_id,
                "synthetic-project",
                "synthetic-loader@synthetic-project.iam.gserviceaccount.com",
                "synthetic-query@synthetic-project.iam.gserviceaccount.com",
            ),
        ).fetchone()[0]

        resource_id = insert_resource(
            conn,
            p,
            model_id,
            version_id,
            approval_id,
        )

    # Independent registry still approves revision 1.
    reviewed = Approval(
        project="synthetic-project",
        workspace_id=workspace_id,
        loader_identity=(
            "synthetic-loader@synthetic-project.iam.gserviceaccount.com"
        ),
        query_identity=(
            "synthetic-query@synthetic-project.iam.gserviceaccount.com"
        ),
        revision=1,
    )

    monkeypatch.setenv("WAREHOUSE_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(
        PermissionError,
        match="Persisted approval differs",
    ):
        resolve_registered_sydney_resource(
            workspace_id,
            model_id,
            version_id,
            resource_id,
            p["owner"],
            registry=Registry([reviewed]),
            expected_approval=reviewed.fingerprint,
            publication_id=5,
            revision=6,
            schema_sha256="a" * 64,
            source_fingerprint="b" * 64,
        )

@pytest.mark.parametrize(
    "identity_field,replacement",
    [
        (
            "loader_identity",
            "alternate-loader@synthetic-project.iam.gserviceaccount.com",
        ),
        (
            "query_identity",
            "alternate-query@synthetic-project.iam.gserviceaccount.com",
        ),
    ],
)
def test_sydney_persisted_service_account_substitution_rejected(
    sydney_modeling_pg,
    monkeypatch,
    identity_field,
    replacement,
):
    p, model_id, version_id = sydney_modeling_pg
    workspace_id = p["workspace"]

    reviewed = Approval(
        project="synthetic-project",
        workspace_id=workspace_id,
        loader_identity=(
            "synthetic-loader@synthetic-project.iam.gserviceaccount.com"
        ),
        query_identity=(
            "synthetic-query@synthetic-project.iam.gserviceaccount.com"
        ),
        revision=1,
    )

    # Insert a different, immutable approval with the same revision.
    # Each parameterized case uses an independent disposable schema.
    identities = {
        "loader_identity": reviewed.loader_identity,
        "query_identity": reviewed.query_identity,
    }
    identities[identity_field] = replacement

    with p["connect"]() as conn:
        approval_id = conn.execute(
            """
            INSERT INTO warehouse_sydney_approvals (
                workspace_id,
                project,
                loader_identity,
                query_identity,
                approval_revision
            )
            VALUES (%s, %s, %s, %s, 1)
            RETURNING approval_id
            """,
            (
                workspace_id,
                reviewed.project,
                identities["loader_identity"],
                identities["query_identity"],
            ),
        ).fetchone()[0]

        resource_id = insert_resource(
            conn,
            p,
            model_id,
            version_id,
            approval_id,
        )

    monkeypatch.setenv("WAREHOUSE_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(
        PermissionError,
        match="Persisted approval differs",
    ):
        resolve_registered_sydney_resource(
            workspace_id,
            model_id,
            version_id,
            resource_id,
            p["owner"],
            registry=Registry([reviewed]),
            expected_approval=reviewed.fingerprint,
            publication_id=5,
            revision=6,
            schema_sha256="a" * 64,
            source_fingerprint="b" * 64,
        )
