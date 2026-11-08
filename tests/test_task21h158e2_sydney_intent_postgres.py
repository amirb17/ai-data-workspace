
"""Sydney Gold-pinned intent registration in disposable PostgreSQL.

No BigQuery network calls or production cloud activation.
"""

from pathlib import Path

import pytest

from app.warehouse.accepted_changes import digest
from tests.test_task21h158e1_gold_postgres import (
    pg,
    source,
    pipeline,
    accepted,
    refresh,
)
from tests.test_task21h158e1_warehouse_postgres import setup
from tests.test_task21h158e2_registration_postgres import (
    create_approval,
    insert_resource,
)


MIGRATIONS = Path("migrations")


def install_sydney_intent_schema(connect):
    """Install only the missing Sydney migrations in test schema."""
    with connect() as conn:
        has_approval = conn.execute(
            "SELECT to_regclass('warehouse_sydney_approvals')"
        ).fetchone()[0]

        if has_approval is None:
            conn.execute(
                (
                    MIGRATIONS
                    / "0046_sydney_warehouse_approval.sql"
                ).read_text(encoding="utf-8")
            )

        has_pins = conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'warehouse_execution_intents'
                  AND column_name = 'gold_verification_id'
            )
            """
        ).fetchone()[0]

        if not has_pins:
            conn.execute(
                (
                    MIGRATIONS
                    / "0047_warehouse_gold_intent_pins.sql"
                ).read_text(encoding="utf-8")
            )


def test_sydney_claim_rejects_stale_gold(
    accepted,
    monkeypatch,
):
    import pytest

    from app.warehouse.reliability import (
        ProtocolError,
    )

    f = accepted("UPSERT")
    provider, development_resource, _ = setup(
        f, monkeypatch
    )
    service = provider.reliability

    refresh(f)

    state_id = service.discover()[0]["state_id"]
    manifest = service.register(state_id, f.store())
    plan = provider.prepare(
        development_resource,
        [manifest],
    )

    install_sydney_intent_schema(f.src[0])

    with f.src[0]() as conn:
        approval_id = create_approval(
            conn,
            f.workspace,
        )
        sydney_resource = insert_resource(
            conn,
            {"workspace": f.workspace, "owner": f.user},
            service.scope[1],
            service.scope[2],
            approval_id,
        )

    monkeypatch.setenv("WAREHOUSE_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")

    intent = service.create_intent(
        sydney_resource,
        "INITIAL_LOAD",
        [manifest["manifest_id"]],
        "stale-gold-claim-test",
        expected=plan.result,
    )

    # Publish a newer accepted source without Gold refresh.
    f.next(b"id,city,amount\n1,B,15\n3,C,30\n")

    with pytest.raises(ProtocolError):
        service.claim(
            intent["operation_id"],
            "synthetic-sydney-worker",
        )

    # Rejected claim must not acquire a writer lease.
    with f.src[0]() as conn:
        lease_count = conn.execute(
            """
            SELECT count(*)
            FROM warehouse_writer_leases
            WHERE resource_id = %s
            """,
            (sydney_resource,),
        ).fetchone()[0]

        run_count = conn.execute(
            """
            SELECT count(*)
            FROM warehouse_runs
            WHERE operation_id = %s
            """,
            (intent["operation_id"],),
        ).fetchone()[0]

    assert lease_count == 0
    assert run_count == 0


def test_sydney_verified_gold_intent_persists_pins(
    accepted,
    monkeypatch,
):
    import pytest

    from app.warehouse.providers.base import (
        ErrorCode,
        ProviderError,
    )
    from app.warehouse.providers.sydney_binding import (
        SydneyBinding,
    )
    from app.warehouse.sydney_binding import (
        Approval,
        Registry,
    )

    f = accepted("UPSERT")

    provider, development_resource, _ = setup(
        f, monkeypatch
    )
    service = provider.reliability

    # 1. Complete independent Gold verification.
    gold_run_id = refresh(f)

    # 2. Register the accepted state.
    state_id = service.discover()[0]["state_id"]
    manifest = service.register(
        state_id,
        f.store(),
    )

    # 3. Build the expected result using the development
    # fake provider. No Sydney execution is attempted.
    plan = provider.prepare(
        development_resource,
        [manifest],
    )

    # 4. Extend the disposable PostgreSQL test schema.
    install_sydney_intent_schema(f.src[0])

    with f.src[0]() as conn:
        approval_id = create_approval(
            conn,
            f.workspace,
        )

        sydney_resource = insert_resource(
            conn,
            {
                "workspace": f.workspace,
                "owner": f.user,
            },
            service.scope[1],
            service.scope[2],
            approval_id,
        )

    monkeypatch.setenv(
        "WAREHOUSE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "APP_ENV",
        "production",
    )

    # 5. Sydney must reject missing independent approval.
    with pytest.raises(ProviderError) as exc:
        provider.prepare(
            sydney_resource,
            [manifest],
        )

    assert exc.value.code == ErrorCode.PERMISSION_DENIED

    # 6. Construct an independently reviewed synthetic
    # approval, not one copied from the database row.
    reviewed = Approval(
        project="synthetic-project",
        workspace_id=f.workspace,
        loader_identity=(
            "synthetic-loader@synthetic-project."
            "iam.gserviceaccount.com"
        ),
        query_identity=(
            "synthetic-query@synthetic-project."
            "iam.gserviceaccount.com"
        ),
        revision=1,
    )

    provider.sydney_registry = Registry([reviewed])
    provider.sydney_expected_approval = reviewed.fingerprint

    # 7. Resolve the approved Sydney binding using the
    # existing authorized PostgreSQL transaction.
    # This does not contact BigQuery.
    with service.transaction() as (
        conn,
        source,
        config,
        settings,
    ):
        binding, context = provider._resolve(
            conn,
            config,
            settings,
            sydney_resource,
        )

    assert isinstance(binding, SydneyBinding)
    assert binding.environment == "production"
    assert binding.location == "australia-southeast1"
    assert binding.project == "synthetic-project"
    assert binding.workspace_id == f.workspace
    assert binding.resource_id == sydney_resource
    assert binding.serving == f"dr_prod_w{f.workspace}"
    assert binding.staging == (
        f"dr_prod_stage_w{f.workspace}"
    )
    assert binding.approval_sha256 == reviewed.fingerprint

    # E2.4: Reject an independently reviewed approval
    # that disagrees with the persisted Sydney approval.
    from dataclasses import replace

    mismatched = replace(
        reviewed,
        revision=2,
    )

    provider.sydney_registry = Registry([mismatched])
    provider.sydney_expected_approval = (
        mismatched.fingerprint
    )

    with service.transaction() as (
        conn,
        source,
        config,
        settings,
    ):
        with pytest.raises(ProviderError) as exc:
            provider._resolve(
                conn,
                config,
                settings,
                sydney_resource,
            )

    assert exc.value.code == ErrorCode.PERMISSION_DENIED

    # Restore the matching approved registry.
    provider.sydney_registry = Registry([reviewed])
    provider.sydney_expected_approval = reviewed.fingerprint

    # E2.4: Sydney execution is simulated using an
    # explicitly injected in-memory BigQuery client.
    from dataclasses import replace

    from app.warehouse.providers.bigquery import BigQueryTarget
    from app.warehouse.providers.sydney_policy import SydneyPolicy
    from tests.bigquery_fake import Client

    sydney_cloud = Client(
        location="australia-southeast1"
    )

    def synthetic_sydney_target(policy, target_binding):
        # The application must supply a disabled Sydney
        # policy. Enable a copy only inside this test.
        assert isinstance(policy, SydneyPolicy)
        assert policy.enabled is False
        assert target_binding.location == "australia-southeast1"
        assert target_binding.approval_sha256 == (
            reviewed.fingerprint
        )

        test_policy = replace(
            policy,
            enabled=True,
        )

        return BigQueryTarget(
            test_policy,
            target_binding,
            client=sydney_cloud,
        )

    provider.target_factory = synthetic_sydney_target

    # This creates only synthetic datasets/tables
    # in the fake client's in-memory dictionaries.
    provider.provision(sydney_resource)

    assert (
        f"synthetic-project.dr_prod_w{f.workspace}"
        in sydney_cloud.datasets
    )
    assert (
        f"synthetic-project.dr_prod_stage_w{f.workspace}"
        in sydney_cloud.datasets
    )

    # 8. Register through the real reliability service.
    intent = service.create_intent(
        sydney_resource,
        "INITIAL_LOAD",
        [manifest["manifest_id"]],
        "sydney-gold-registration-test",
        expected=plan.result,
    )

    assert intent["gold_verification_id"] is not None
    assert intent["gold_run_id"] == gold_run_id

    pins = intent["body"]["gold_verification"]

    assert pins["verification_id"] == intent[
        "gold_verification_id"
    ]
    assert pins["gold_run_id"] == gold_run_id
    assert pins["source_fingerprint"] == intent[
        "gold_source_fingerprint"
    ]

    expected_checksum = digest([
        "datarise-execution-intent",
        1,
        intent["body"],
        intent["application_token"],
    ])

    assert intent["intent_sha256"] == expected_checksum

    # 9. A retry must reuse the immutable operation.
    again = service.create_intent(
        sydney_resource,
        "INITIAL_LOAD",
        [manifest["manifest_id"]],
        "sydney-gold-registration-test",
        expected=plan.result,
    )

    assert again["operation_id"] == intent["operation_id"]
    assert again["gold_verification_id"] == pins[
        "verification_id"
    ]

    with f.src[0]() as conn:
        count = conn.execute(
            """
            SELECT count(*)
            FROM warehouse_execution_intents
            WHERE operation_id = %s
            """,
            (intent["operation_id"],),
        ).fetchone()[0]

    assert count == 1

    # 10. Verified, unchanged Gold can be claimed.
    claim = service.claim(
        intent["operation_id"],
        "synthetic-sydney-worker",
        lease_seconds=60,
    )

    assert claim["run_id"] > 0
    assert claim["fence"] >= 1

    # 11. Execute against the in-memory Sydney BigQuery client.
    # ProviderService performs planning, provider application,
    # receipt recording and reconciliation.
    response = provider.execute(
        intent["operation_id"],
        claim["run_id"],
        claim["fence"],
    )

    assert response["publication_eligible"] is True
    assert response["reconciliation_id"] > 0

    # 12. Successful reconciliation alone must not
    # advance the PostgreSQL publication head.
    before_publication = service.read_progress(
        sydney_resource
    )

    assert before_publication["head"] is None

    # 13. Publish using the existing fenced reliability
    # protocol. Gold must still be current.
    publication = service.publish(
        intent["operation_id"],
        claim["run_id"],
        claim["fence"],
        response["reconciliation_id"],
    )

    assert publication["operation_id"] == (
        intent["operation_id"]
    )
    assert publication["resource_id"] == sydney_resource

    # 14. Verify durable publication and checkpoint.
    progress = service.read_progress(sydney_resource)

    assert progress["head"] is not None
    assert (
        progress["head"]["publication_id"]
        == publication["publication_id"]
    )

    assert progress["checkpoint"] is not None
    assert progress["checkpoint"]["position"] == (
        manifest["position"]
    )

    # 15. Verify the immutable BigQuery application journal
    # contains exactly one application for this intent.
    journal = binding.physical("journal")

    matching = [
        row
        for row in sydney_cloud.rows[journal]
        if row["application_token"]
        == intent["application_token"]
    ]

    assert len(matching) == 1
    assert matching[0]["intent_sha256"] == (
        intent["intent_sha256"]
    )

    # 16. A completed warehouse run cannot execute again.
    from app.warehouse.reliability import (
        Decision,
        ProtocolError,
    )

    with pytest.raises(ProtocolError) as exc:
        provider.execute(
            intent["operation_id"],
            claim["run_id"],
            claim["fence"],
        )

    assert exc.value.code == Decision.FENCED

    # A rejected replay must not create additional
    # physical application journal entries.
    matching_after = [
        row
        for row in sydney_cloud.rows[journal]
        if row["application_token"]
        == intent["application_token"]
    ]

    assert len(matching_after) == 1

    # Published progress must remain unchanged.
    after_replay = service.read_progress(sydney_resource)

    assert (
        after_replay["head"]["publication_id"]
        == publication["publication_id"]
    )
    assert (
        after_replay["checkpoint"]["position"]
        == manifest["position"]
    )

def test_sydney_plan_rejects_gold_drift_after_claim(
    accepted,
    monkeypatch,
):
    import pytest

    from app.warehouse.reliability import ProtocolError

    f = accepted("UPSERT")

    provider, development_resource, _ = setup(
        f, monkeypatch
    )

    service = provider.reliability

    refresh(f)

    state_id = service.discover()[0]["state_id"]
    manifest = service.register(
        state_id,
        f.store(),
    )

    plan = provider.prepare(
        development_resource,
        [manifest],
    )

    install_sydney_intent_schema(f.src[0])

    with f.src[0]() as conn:
        approval_id = create_approval(
            conn,
            f.workspace,
        )

        sydney_resource = insert_resource(
            conn,
            {
                "workspace": f.workspace,
                "owner": f.user,
            },
            service.scope[1],
            service.scope[2],
            approval_id,
        )

    monkeypatch.setenv(
        "WAREHOUSE_ENABLED", "true"
    )
    monkeypatch.setenv(
        "APP_ENV", "production"
    )

    intent = service.create_intent(
        sydney_resource,
        "INITIAL_LOAD",
        [manifest["manifest_id"]],
        "plan-drift-test",
        expected=plan.result,
    )

    claim = service.claim(
        intent["operation_id"],
        "synthetic-sydney-worker",
    )

    # The accepted dataset changes after the claim.
    # No new Gold verification is published.
    f.next(
        b"id,city,amount\n1,B,15\n3,C,30\n"
    )

    # A stale Sydney intent must be rejected before
    # the provider factory or BigQuery client is accessed.
    with monkeypatch.context() as patch:
        patch.setattr(
            provider,
            "_target",
            lambda binding: pytest.fail(
                "Provider accessed with stale Sydney Gold"
            ),
        )

        with pytest.raises(
            ProtocolError,
            match="SOURCE_DRIFT",
        ):
            provider.execute(
                intent["operation_id"],
                claim["run_id"],
                claim["fence"],
            )

    with pytest.raises(ProtocolError):
        service.plan(
            intent["operation_id"],
            claim["run_id"],
            claim["fence"],
        )

    # No executable plan must be recorded.
    with f.src[0]() as conn:
        planned = conn.execute(
            """
            SELECT count(*)
            FROM warehouse_run_journal
            WHERE warehouse_run_id = %s
              AND stage = 'PLANNED'
            """,
            (claim["run_id"],),
        ).fetchone()[0]

    assert planned == 0

def test_sydney_publish_rejects_stale_gold_without_state_changes(
    accepted,
    monkeypatch,
):
    import pytest

    from app.warehouse.reliability import ProtocolError

    f = accepted("UPSERT")

    provider, development_resource, _ = setup(
        f, monkeypatch
    )
    service = provider.reliability

    refresh(f)

    state_id = service.discover()[0]["state_id"]
    manifest = service.register(state_id, f.store())

    plan = provider.prepare(
        development_resource,
        [manifest],
    )

    install_sydney_intent_schema(f.src[0])

    with f.src[0]() as conn:
        approval_id = create_approval(conn, f.workspace)

        sydney_resource = insert_resource(
            conn,
            {"workspace": f.workspace, "owner": f.user},
            service.scope[1],
            service.scope[2],
            approval_id,
        )

    monkeypatch.setenv("WAREHOUSE_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")

    intent = service.create_intent(
        sydney_resource,
        "INITIAL_LOAD",
        [manifest["manifest_id"]],
        "publication-drift-test",
        expected=plan.result,
    )

    claim = service.claim(
        intent["operation_id"],
        "synthetic-sydney-worker",
        lease_seconds=900,
    )

    # Gold changes after an intent has been claimed.
    f.next(
        b"id,city,amount\n1,B,15\n3,C,30\n"
    )

    # Publication must fail on Gold drift before any
    # publication or checkpoint changes can occur.
    with pytest.raises(ProtocolError):
        service.publish(
            intent["operation_id"],
            claim["run_id"],
            claim["fence"],
            reconciliation_id=1,
        )

    with f.src[0]() as conn:
        publication_count = conn.execute(
            """
            SELECT count(*)
            FROM warehouse_publications
            WHERE operation_id = %s
            """,
            (intent["operation_id"],),
        ).fetchone()[0]

        head_count = conn.execute(
            """
            SELECT count(*)
            FROM warehouse_publication_heads
            WHERE resource_id = %s
            """,
            (sydney_resource,),
        ).fetchone()[0]

        checkpoint_count = conn.execute(
            """
            SELECT count(*)
            FROM warehouse_checkpoints
            WHERE resource_id = %s
            """,
            (sydney_resource,),
        ).fetchone()[0]

        run_status = conn.execute(
            """
            SELECT status
            FROM warehouse_runs
            WHERE warehouse_run_id = %s
            """,
            (claim["run_id"],),
        ).fetchone()[0]

    assert publication_count == 0
    assert head_count == 0
    assert checkpoint_count == 0
    assert run_status != "SUCCEEDED"

def test_sydney_post_reconciliation_gold_drift_blocks_publication(
    accepted,
    monkeypatch,
):
    """A reconciled Sydney intent cannot publish stale Gold."""

    import pytest

    from app.warehouse.reliability import (
        Decision,
        ProtocolError,
        Receipt,
    )

    f = accepted("UPSERT")

    provider, development_resource, _ = setup(
        f, monkeypatch
    )
    service = provider.reliability

    # 1. Prepare the existing verified Gold source.
    refresh(f)

    state_id = service.discover()[0]["state_id"]
    manifest = service.register(state_id, f.store())

    plan = provider.prepare(
        development_resource,
        [manifest],
    )

    # 2. Install Sydney test schema and approval.
    install_sydney_intent_schema(f.src[0])

    with f.src[0]() as conn:
        approval_id = create_approval(conn, f.workspace)

        sydney_resource = insert_resource(
            conn,
            {"workspace": f.workspace, "owner": f.user},
            service.scope[1],
            service.scope[2],
            approval_id,
        )

    monkeypatch.setenv("WAREHOUSE_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")

    # 3. Register, claim and plan while Gold is current.
    intent = service.create_intent(
        sydney_resource,
        "INITIAL_LOAD",
        [manifest["manifest_id"]],
        "post-reconciliation-drift-test",
        expected=plan.result,
    )

    operation_id = intent["operation_id"]

    claim = service.claim(
        operation_id,
        "synthetic-sydney-worker",
        lease_seconds=900,
    )

    run_id = claim["run_id"]
    fence = claim["fence"]

    service.plan(operation_id, run_id, fence)

    # 4. Simulate successful external execution.
    # This exercises the receipt protocol, not BigQuery.
    receipt = Receipt(
        operation_id=operation_id,
        run_id=run_id,
        application_token=intent["application_token"],
        intent_sha256=intent["intent_sha256"],
        resource_generation=intent["generation"],
        outcome="SUCCEEDED",
        result=plan.result,
        receipt_reference="synthetic-sydney-success",
    )

    decision = service.record_receipt(
        operation_id,
        run_id,
        fence,
        receipt,
    )

    assert decision == Decision.ALREADY_APPLIED

    reconciliation = service.reconcile(
        operation_id,
        run_id,
        fence,
    )

    assert reconciliation["passed"] is True

    # 5. Source changes AFTER successful reconciliation.
    f.next(
        b"id,city,amount\n1,B,15\n3,C,30\n"
    )

    # 6. Publication must reject outdated Gold.
    with pytest.raises(
        ProtocolError,
        match="SOURCE_DRIFT",
    ):
        service.publish(
            operation_id,
            run_id,
            fence,
            reconciliation["reconciliation_id"],
        )

    # 7. Ensure no publication state was advanced.
    with f.src[0]() as conn:
        publications = conn.execute(
            """
            SELECT count(*)
            FROM warehouse_publications
            WHERE operation_id = %s
            """,
            (operation_id,),
        ).fetchone()[0]

        heads = conn.execute(
            """
            SELECT count(*)
            FROM warehouse_publication_heads
            WHERE resource_id = %s
            """,
            (sydney_resource,),
        ).fetchone()[0]

        checkpoints = conn.execute(
            """
            SELECT count(*)
            FROM warehouse_checkpoints
            WHERE resource_id = %s
            """,
            (sydney_resource,),
        ).fetchone()[0]

        status = conn.execute(
            """
            SELECT status
            FROM warehouse_runs
            WHERE warehouse_run_id = %s
            """,
            (run_id,),
        ).fetchone()[0]

        reconciled = conn.execute(
            """
            SELECT passed
            FROM warehouse_reconciliations
            WHERE reconciliation_id = %s
            """,
            (reconciliation["reconciliation_id"],),
        ).fetchone()[0]

    assert publications == 0
    assert heads == 0
    assert checkpoints == 0
    assert status != "SUCCEEDED"

    # Reconciliation evidence remains intact for review.
    assert reconciled is True

@pytest.fixture
def approved_sydney_provider(accepted, monkeypatch):
    """Verified Gold and approved in-memory Sydney provider."""

    from dataclasses import replace
    from types import SimpleNamespace

    from app.warehouse.providers.bigquery import BigQueryTarget
    from app.warehouse.providers.sydney_policy import SydneyPolicy
    from app.warehouse.sydney_binding import Approval, Registry
    from tests.bigquery_fake import Client

    f = accepted("UPSERT")

    provider, development_resource, _ = setup(
        f,
        monkeypatch,
    )

    service = provider.reliability

    gold_run_id = refresh(f)

    state_id = service.discover()[0]["state_id"]
    manifest = service.register(
        state_id,
        f.store(),
    )

    # Prepare with the existing development fake.
    plan = provider.prepare(
        development_resource,
        [manifest],
    )

    install_sydney_intent_schema(f.src[0])

    with f.src[0]() as conn:
        approval_id = create_approval(conn, f.workspace)

        sydney_resource = insert_resource(
            conn,
            {"workspace": f.workspace, "owner": f.user},
            service.scope[1],
            service.scope[2],
            approval_id,
        )

    reviewed = Approval(
        project="synthetic-project",
        workspace_id=f.workspace,
        loader_identity=(
            "synthetic-loader@synthetic-project."
            "iam.gserviceaccount.com"
        ),
        query_identity=(
            "synthetic-query@synthetic-project."
            "iam.gserviceaccount.com"
        ),
        revision=1,
    )

    provider.sydney_registry = Registry([reviewed])
    provider.sydney_expected_approval = reviewed.fingerprint

    cloud = Client(location="australia-southeast1")

    def factory(policy, binding):
        assert isinstance(policy, SydneyPolicy)
        assert policy.enabled is False
        assert binding.approval_sha256 == reviewed.fingerprint

        return BigQueryTarget(
            replace(policy, enabled=True),
            binding,
            client=cloud,
        )

    provider.target_factory = factory

    monkeypatch.setenv("WAREHOUSE_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")

    provider.provision(sydney_resource)

    return SimpleNamespace(
        f=f,
        provider=provider,
        service=service,
        resource=sydney_resource,
        manifest=manifest,
        plan=plan,
        cloud=cloud,
    )

def test_sydney_lost_response_recovers_with_new_fence(
    approved_sydney_provider,
):
    import pytest

    from app.warehouse.providers.base import ProviderError

    case = approved_sydney_provider
    service = case.service
    provider = case.provider

    intent = service.create_intent(
        case.resource,
        "INITIAL_LOAD",
        [case.manifest["manifest_id"]],
        "sydney-lost-response-recovery",
        expected=case.plan.result,
    )

    first = service.claim(
        intent["operation_id"],
        "sydney-first-worker",
        lease_seconds=900,
    )

    # Fake BigQuery commits the transaction but loses
    # its response before PostgreSQL records success.
    case.cloud.lose_response = True

    with pytest.raises(ProviderError) as exc:
        provider.execute(
            intent["operation_id"],
            first["run_id"],
            first["fence"],
        )

    assert exc.value.uncertain is True

    assert service.read_progress(
        case.resource
    )["head"] is None

    # Simulate expiry of the abandoned writer lease.
    # Allowed only in this disposable PostgreSQL schema.
    with case.f.src[0]() as conn:
        conn.execute(
            """
            UPDATE warehouse_writer_leases
            SET expires_at = clock_timestamp()
                             - interval '1 second'
            WHERE resource_id = %s
            """,
            (case.resource,),
        )

    # A new fenced claim recovers the SAME operation.
    recovered = service.claim(
        intent["operation_id"],
        "sydney-recovery-worker",
        lease_seconds=900,
    )

    assert recovered["fence"] > first["fence"]

    response = provider.execute(
        intent["operation_id"],
        recovered["run_id"],
        recovered["fence"],
    )

    assert response["publication_eligible"] is True

    publication = service.publish(
        intent["operation_id"],
        recovered["run_id"],
        recovered["fence"],
        response["reconciliation_id"],
    )

    progress = service.read_progress(case.resource)

    assert (
        progress["head"]["publication_id"]
        == publication["publication_id"]
    )

    assert (
        progress["checkpoint"]["position"]
        == case.manifest["position"]
    )

    # The original committed application must be reused,
    # not applied again under a different token.
    with service.transaction() as (
        conn,
        source,
        config,
        settings,
    ):
        binding, _ = provider._resolve(
            conn,
            config,
            settings,
            case.resource,
        )

    journal = binding.physical("journal")

    matching = [
        row
        for row in case.cloud.rows[journal]
        if row["application_token"]
        == intent["application_token"]
    ]

    assert len(matching) == 1

    # Only one successful application mutation job.
    apply_jobs = [
        call
        for call in case.cloud.calls
        if call[0] == "query"
        and call[1].startswith("BEGIN TRANSACTION")
    ]

    assert len(apply_jobs) == 1

def test_sydney_incremental_delivery_preserves_publication_chain(
    approved_sydney_provider,
):
    case = approved_sydney_provider
    f = case.f
    provider = case.provider
    service = case.service

    # 1. Initial verified Gold delivery.
    first_intent = service.create_intent(
        case.resource,
        "INITIAL_LOAD",
        [case.manifest["manifest_id"]],
        "sydney-incremental-first",
        expected=case.plan.result,
    )

    first_claim = service.claim(
        first_intent["operation_id"],
        "sydney-worker-first",
        lease_seconds=900,
    )

    first_response = provider.execute(
        first_intent["operation_id"],
        first_claim["run_id"],
        first_claim["fence"],
    )

    first_publication = service.publish(
        first_intent["operation_id"],
        first_claim["run_id"],
        first_claim["fence"],
        first_response["reconciliation_id"],
    )

    first_progress = service.read_progress(case.resource)

    assert first_progress["head"]["revision"] == 1
    assert first_progress["checkpoint"]["position"] == (
        case.manifest["position"]
    )

    # 2. Publish a new accepted source state.
    f.next(
        b"id,city,amount\n1,B,15\n3,C,30\n"
    )

    # Refresh Gold for the new accepted state.
    refresh(f)

    second_state_id = service.discover()[0]["state_id"]
    second_manifest = service.register(
        second_state_id,
        f.store(),
    )

    assert second_manifest["manifest_id"] != (
        case.manifest["manifest_id"]
    )

    # 3. Calculate the incremental expected result.
    # This reads previously published candidate state
    # using the in-memory Sydney BigQuery client.
    second_plan = provider.prepare(
        case.resource,
        [second_manifest],
    )

    # 4. Create and execute the next immutable intent.
    second_intent = service.create_intent(
        case.resource,
        "INCREMENTAL",
        [second_manifest["manifest_id"]],
        "sydney-incremental-second",
        expected=second_plan.result,
    )

    assert second_intent["gold_verification_id"] is not None

    second_claim = service.claim(
        second_intent["operation_id"],
        "sydney-worker-second",
        lease_seconds=900,
    )

    second_response = provider.execute(
        second_intent["operation_id"],
        second_claim["run_id"],
        second_claim["fence"],
    )

    second_publication = service.publish(
        second_intent["operation_id"],
        second_claim["run_id"],
        second_claim["fence"],
        second_response["reconciliation_id"],
    )

    # 5. Verify publication lineage and progress.
    progress = service.read_progress(case.resource)

    assert progress["head"]["revision"] == 2

    assert (
        progress["head"]["publication_id"]
        == second_publication["publication_id"]
    )

    assert (
        second_publication["previous_publication_id"]
        == first_publication["publication_id"]
    )

    assert progress["checkpoint"]["position"] == (
        second_manifest["position"]
    )

    # 6. Verify each immutable token was applied once.
    with service.transaction() as (
        conn,
        source,
        config,
        settings,
    ):
        binding, _ = provider._resolve(
            conn,
            config,
            settings,
            case.resource,
        )

    journal = case.cloud.rows[
        binding.physical("journal")
    ]

    tokens = [
        first_intent["application_token"],
        second_intent["application_token"],
    ]

    assert tokens[0] != tokens[1]

    for token in tokens:
        assert sum(
            row["application_token"] == token
            for row in journal
        ) == 1

    assert len(journal) == 2
