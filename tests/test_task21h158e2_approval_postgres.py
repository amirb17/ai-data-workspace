
"""Disposable PostgreSQL tests for Sydney approval storage."""

from pathlib import Path

import psycopg
import pytest

from tests.test_warehouse_foundation import pg


MIGRATION = Path(
    "migrations/0046_sydney_warehouse_approval.sql"
)


def test_sydney_approval_is_immutable(pg):
    workspace_id = pg["workspace"]

    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))

        row = conn.execute(
            """
            INSERT INTO warehouse_sydney_approvals (
                workspace_id,
                project,
                loader_identity,
                query_identity,
                approval_revision
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING approval_id
            """,
            (
                workspace_id,
                "synthetic-project",
                "synthetic-loader@synthetic-project.iam.gserviceaccount.com",
                "synthetic-query@synthetic-project.iam.gserviceaccount.com",
                1,
            ),
        ).fetchone()

        approval_id = row[0]

    # A separate transaction avoids leaving the original
    # transaction in an aborted state.
    with pytest.raises(psycopg.errors.RaiseException):
        with pg["connect"]() as conn:
            conn.execute(
                """
                UPDATE warehouse_sydney_approvals
                SET approval_revision = 2
                WHERE approval_id = %s
                """,
                (approval_id,),
            )

    with pytest.raises(psycopg.errors.RaiseException):
        with pg["connect"]() as conn:
            conn.execute(
                """
                DELETE FROM warehouse_sydney_approvals
                WHERE approval_id = %s
                """,
                (approval_id,),
            )

    with pg["connect"]() as conn:
        original = conn.execute(
            """
            SELECT approval_revision, project, location
            FROM warehouse_sydney_approvals
            WHERE approval_id = %s
            """,
            (approval_id,),
        ).fetchone()

    assert original == (
        1,
        "synthetic-project",
        "australia-southeast1",
    )

@pytest.mark.parametrize(
    "loader,query",
    [
        (
            "loader@another-project.iam.gserviceaccount.com",
            "synthetic-query@synthetic-project.iam.gserviceaccount.com",
        ),
        (
            "synthetic-loader@synthetic-project.iam.gserviceaccount.com",
            "query@another-project.iam.gserviceaccount.com",
        ),
        (
            "invalid-email",
            "synthetic-query@synthetic-project.iam.gserviceaccount.com",
        ),
        (
            "same@synthetic-project.iam.gserviceaccount.com",
            "same@synthetic-project.iam.gserviceaccount.com",
        ),
    ],
)
def test_invalid_sydney_identities_rejected(pg, loader, query):
    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))

    with pytest.raises(psycopg.errors.CheckViolation):
        with pg["connect"]() as conn:
            conn.execute(
                """
                INSERT INTO warehouse_sydney_approvals (
                    workspace_id,
                    project,
                    loader_identity,
                    query_identity,
                    approval_revision
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    pg["workspace"],
                    "synthetic-project",
                    loader,
                    query,
                    1,
                ),
            )

@pytest.mark.parametrize(
    "aws_region,location",
    [
        ("ap-south-1", "australia-southeast1"),
        ("ap-southeast-2", "asia-south1"),
        ("us-east-1", "us-central1"),
    ],
)
def test_invalid_sydney_regions_rejected(
    pg, aws_region, location
):
    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))

    with pytest.raises(psycopg.errors.CheckViolation):
        with pg["connect"]() as conn:
            conn.execute(
                """
                INSERT INTO warehouse_sydney_approvals (
                    workspace_id,
                    project,
                    aws_region,
                    location,
                    loader_identity,
                    query_identity,
                    approval_revision
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    pg["workspace"],
                    "synthetic-project",
                    aws_region,
                    location,
                    "synthetic-loader@synthetic-project.iam.gserviceaccount.com",
                    "synthetic-query@synthetic-project.iam.gserviceaccount.com",
                    1,
                ),
            )


def test_duplicate_approval_revision_rejected(pg):
    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))

    values = (
        pg["workspace"],
        "synthetic-project",
        "synthetic-loader@synthetic-project.iam.gserviceaccount.com",
        "synthetic-query@synthetic-project.iam.gserviceaccount.com",
        1,
    )

    statement = """
        INSERT INTO warehouse_sydney_approvals (
            workspace_id,
            project,
            loader_identity,
            query_identity,
            approval_revision
        )
        VALUES (%s, %s, %s, %s, %s)
    """

    with pg["connect"]() as conn:
        conn.execute(statement, values)

    with pytest.raises(psycopg.errors.UniqueViolation):
        with pg["connect"]() as conn:
            conn.execute(statement, values)



def test_sydney_approval_truncate_rejected(pg):
    with pg["connect"]() as conn:
        conn.execute(
            MIGRATION.read_text(encoding="utf-8")
        )

    # PostgreSQL may reject TRUNCATE before our trigger executes
    # because warehouse_resources references this table.
    with pytest.raises(
        (
            psycopg.errors.RaiseException,
            psycopg.errors.FeatureNotSupported,
        )
    ):
        with pg["connect"]() as conn:
            conn.execute(
                "TRUNCATE warehouse_sydney_approvals"
            )


def test_sydney_resource_approval_foreign_key_exists(pg):
    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))

        row = conn.execute(
            """
            SELECT
                conname,
                pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid =
                'warehouse_resources'::regclass
              AND conname =
                'warehouse_resource_sydney_approval_fk'
            """
        ).fetchone()

    assert row is not None

    name, definition = row

    assert name == "warehouse_resource_sydney_approval_fk"
    assert "FOREIGN KEY (workspace_id, sydney_approval_id)" in definition
    assert "warehouse_sydney_approvals" in definition


def test_sydney_resource_approval_column_nullable(pg):
    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))

        row = conn.execute(
            """
            SELECT
                is_nullable,
                data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'warehouse_resources'
              AND column_name = 'sydney_approval_id'
            """
        ).fetchone()

    assert row == ("YES", "bigint")

def test_sydney_approval_truncate_cascade_rejected(pg):
    with pg["connect"]() as conn:
        conn.execute(
            MIGRATION.read_text(encoding="utf-8")
        )

    with pytest.raises(psycopg.errors.RaiseException):
        with pg["connect"]() as conn:
            conn.execute(
                "TRUNCATE warehouse_sydney_approvals CASCADE"
            )

@pytest.mark.parametrize(
    "loader,query",
    [
        (
            "abc@synthetic-project.iam.gserviceaccount.com",
            "synthetic-query@synthetic-project.iam.gserviceaccount.com",
        ),
        (
            "synthetic-loader@synthetic-project.iam.gserviceaccount.com",
            "abc@synthetic-project.iam.gserviceaccount.com",
        ),
    ],
)
def test_short_service_account_names_rejected(pg, loader, query):
    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))

    with pytest.raises(psycopg.errors.CheckViolation):
        with pg["connect"]() as conn:
            conn.execute(
                """
                INSERT INTO warehouse_sydney_approvals (
                    workspace_id,
                    project,
                    loader_identity,
                    query_identity,
                    approval_revision
                )
                VALUES (%s, %s, %s, %s, 1)
                """,
                (
                    pg["workspace"],
                    "synthetic-project",
                    loader,
                    query,
                ),
            )

def test_sydney_approval_has_no_public_privileges(pg):
    with pg["connect"]() as conn:
        conn.execute(
            MIGRATION.read_text(encoding="utf-8")
        )

        result = conn.execute(
            """
            SELECT
                has_table_privilege(
                    'public',
                    'warehouse_sydney_approvals',
                    'SELECT'
                ),
                has_table_privilege(
                    'public',
                    'warehouse_sydney_approvals',
                    'INSERT'
                ),
                has_table_privilege(
                    'public',
                    'warehouse_sydney_approvals',
                    'UPDATE'
                ),
                has_table_privilege(
                    'public',
                    'warehouse_sydney_approvals',
                    'DELETE'
                )
            """
        ).fetchone()

    assert result == (False, False, False, False)

def test_sydney_registration_guard_security_configuration(pg):
    with pg["connect"]() as conn:
        conn.execute(
            MIGRATION.read_text(encoding="utf-8")
        )

        row = conn.execute(
            """
            SELECT
                p.prosecdef,
                p.proconfig,
                has_function_privilege(
                    'public',
                    p.oid,
                    'EXECUTE'
                )
            FROM pg_proc p
            JOIN pg_namespace n
              ON n.oid = p.pronamespace
            WHERE n.nspname = current_schema()
              AND p.proname =
                  'warehouse_sydney_resource_registration_guard'
              AND p.pronargs = 0
            """
        ).fetchone()

        assert row is not None

        security_definer, configuration, public_execute = row

        assert security_definer is True
        assert public_execute is False

        schema_name = conn.execute(
            "SELECT current_schema()"
        ).fetchone()[0]

        expected_path = (
            f"search_path=pg_catalog,{schema_name},pg_temp"
        )

        normalized_configuration = [
            setting.replace(" ", "")
            for setting in configuration
        ]

        assert expected_path in normalized_configuration
