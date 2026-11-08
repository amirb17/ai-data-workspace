
"""Migration 0047 verification in disposable PostgreSQL schemas."""

from pathlib import Path

from tests.test_warehouse_foundation import pg, approved

from tests.test_task21h158e2_registration_postgres import (
    sydney_pg,
)

MIGRATION_0047 = Path(
    "migrations/0047_warehouse_gold_intent_pins.sql"
)


def test_gold_intent_migration_applies(sydney_pg):
    pg, model_id, version_id = sydney_pg


    with pg["connect"]() as conn:
        # The base pg fixture applies migrations through 0029.
        # The sydney_pg fixture has already applied 0046.
        #
        # Apply the missing intermediate migrations in order.
        # Do not reapply 0046 or alter the public schema.
        migration_dir = Path("migrations")

        for migration in sorted(migration_dir.glob("*.sql")):
            number = int(migration.name.split("_", 1)[0])

            if 30 <= number <= 45:
                conn.execute(
                    migration.read_text(encoding="utf-8")
                )

        conn.execute(
            MIGRATION_0047.read_text(encoding="utf-8")
        )

        # Keep the existing columns/constraints/trigger
        # assertions immediately below this section.


        columns = {
            row[0]: row[1]
            for row in conn.execute(
                """
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'warehouse_execution_intents'
                  AND column_name IN (
                      'gold_verification_id',
                      'gold_source_fingerprint',
                      'gold_run_id'
                  )
                """
            ).fetchall()
        }

        assert columns == {
            "gold_verification_id": "YES",
            "gold_source_fingerprint": "YES",
            "gold_run_id": "YES",
        }

        constraints = {
            row[0]
            for row in conn.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid =
                    'warehouse_execution_intents'::regclass
                """
            ).fetchall()
        }

        assert "warehouse_intent_gold_verification_fk" in constraints
        assert "warehouse_intent_gold_run_fk" in constraints
        assert "warehouse_intent_gold_pin_consistency" in constraints

        trigger = conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_trigger
                WHERE tgrelid =
                    'warehouse_execution_intents'::regclass
                  AND tgname =
                    'warehouse_sydney_intent_gold_insert'
                  AND NOT tgisinternal
            )
            """
        ).fetchone()[0]

        assert trigger is True

def test_gold_pin_columns_reject_incomplete_values(sydney_pg):
    pg, _, _ = sydney_pg

    with pg["connect"]() as conn:
        migration_dir = Path("migrations")

        for migration in sorted(migration_dir.glob("*.sql")):
            number = int(migration.name.split("_", 1)[0])
            if 30 <= number <= 45:
                conn.execute(
                    migration.read_text(encoding="utf-8")
                )

        conn.execute(
            MIGRATION_0047.read_text(encoding="utf-8")
        )

        # The CHECK constraint applies to every intent,
        # regardless of its target environment.
        constraint = conn.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid =
                'warehouse_execution_intents'::regclass
              AND conname =
                'warehouse_intent_gold_pin_consistency'
            """
        ).fetchone()

        assert constraint is not None

        definition = constraint[0]

        assert "gold_verification_id" in definition
        assert "gold_source_fingerprint" in definition
        assert "gold_run_id" in definition
        assert "IS NULL" in definition
        assert "IS NOT NULL" in definition
