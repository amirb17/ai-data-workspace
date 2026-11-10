"""414D3: DB-bound compiler comparison remains non-authorizing."""
from dataclasses import replace
from pathlib import Path

import pytest

from app.query_governance.providers.base import CompiledQuery
from app.services.warehouse_sql_compilation_comparison_service import (
    compare_compilation_to_registered_publications as compare,
)
from app.warehouse.sql_physical_binding import (
    InternalDraftBinding,InternalModelBinding,
)
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_reliability import reliability_pg
from tests.test_warehouse_query_reservation_publication_links_postgres import (
    linked_pg,setup_attempt,insert_link,
)
from tests.test_warehouse_query_publication_retirement_postgres import retirement_pg


def compiled_fixture(f,publication,attempt):
    with f.pg["connect"]() as conn:
        resource=conn.execute(
            """SELECT warehouse_model_id,warehouse_model_version_id,
                      generation,physical_reference
               FROM warehouse_resources WHERE resource_id=%s""",
            (f.resource,),
        ).fetchone()
    model,version,generation,physical=resource
    physical_name=(
        f"{physical['project']}.dr_dev_w{f.pg['workspace']}."
        f"m{model}_v{version}_g{generation}_p{publication['publication_id']}"
        f"_r{publication['revision']}"
    )
    binding=InternalDraftBinding((
        InternalModelBinding(
            model,version,f.resource,generation,
            publication["publication_id"],publication["revision"],
            1,"c"*64,"d"*64,physical_name,
        ),
    ),())
    query=CompiledQuery(
        workspace_id=f.pg["workspace"],request_token=attempt.request_token,
        intent_sha256="b"*64,sql=f"SELECT 1 FROM `{physical_name}` LIMIT 1",
        physical_tables=(physical_name,),compiler_version=2,
    )
    return binding,query


def test_owned_compilation_matches_exact_durable_publication_but_not_authorized(
    retirement_pg,
):
    f,publication=retirement_pg
    governance,attempt,reservation=setup_attempt(f,publication)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    binding,query=compiled_fixture(f,publication,attempt)
    result=compare(
        governance=governance,attempt_handle=attempt.attempt_handle,
        query=query,binding=binding,
    )
    assert result.consistent
    assert result.reason=="CONSISTENT_UNTRUSTED_COMPILATION"
    assert not result.execution_authorized
    assert not result.reservation_claimed
    assert not result.provider_dispatched
    assert governance.reservation(reservation)["status"]=="ACTIVE"


def test_wrong_physical_table_fails_closed(retirement_pg):
    f,pub=retirement_pg
    governance,attempt,reservation=setup_attempt(f,pub)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    binding,query=compiled_fixture(f,pub,attempt)
    wrong=replace(binding,models=(replace(binding.models[0],publication_id=999),))
    with pytest.raises(ValueError,match="compiled publication evidence"):
        compare(governance=governance,attempt_handle=attempt.attempt_handle,
                query=query,binding=wrong)
    assert governance.reservation(reservation)["status"]=="ACTIVE"


def test_mismatched_query_intent_hash_is_not_accepted(retirement_pg):
    f,pub=retirement_pg
    governance,attempt,reservation=setup_attempt(f,pub)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    binding,query=compiled_fixture(f,pub,attempt)
    bad=replace(query,intent_sha256="e"*64)
    result=compare(governance=governance,attempt_handle=attempt.attempt_handle,
                   query=bad,binding=binding)
    assert result.reason=="COMPILED_RESERVATION_MISMATCH"
    assert not result.execution_authorized


def test_wrong_binding_version_is_rejected_against_database(retirement_pg):
    f,pub=retirement_pg
    governance,attempt,reservation=setup_attempt(f,pub)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    binding,query=compiled_fixture(f,pub,attempt)
    tampered=replace(binding,models=(replace(binding.models[0],
        model_version_id=binding.models[0].model_version_id+1),))
    name=tampered.models[0].physical_table.replace(
        f"_v{binding.models[0].model_version_id}_",
        f"_v{tampered.models[0].model_version_id}_",
    )
    tampered=replace(tampered,models=(replace(tampered.models[0],
        physical_table=name),))
    changed=replace(query,physical_tables=(name,))
    result=compare(governance=governance,attempt_handle=attempt.attempt_handle,
                   query=changed,binding=tampered)
    assert result.reason=="COMPILED_PUBLICATION_PINS_MISMATCH"


def test_absent_link_rejected_without_execution(retirement_pg):
    f,pub=retirement_pg
    governance,attempt,_=setup_attempt(f,pub)
    binding,query=compiled_fixture(f,pub,attempt)
    result=compare(governance=governance,attempt_handle=attempt.attempt_handle,
                   query=query,binding=binding)
    assert result.reason=="LINK_MISSING"
    assert not result.consistent


def test_no_execute_or_claim_surface():
    source=(Path(__file__).resolve().parents[1]/
        "app/services/warehouse_sql_compilation_comparison_service.py"
        ).read_text(encoding="utf-8")
    assert "execution_authorized: bool = False" in source
    assert "CONSISTENT_UNTRUSTED_COMPILATION" in source
    assert "provider.execute(" not in source
    assert "governance.claim(" not in source
