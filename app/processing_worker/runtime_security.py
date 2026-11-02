"""Fail-closed deployment gates. No credentials, role creation or permission changes."""
import os
import re
import ipaddress


def require_runtime(conn, purpose, *, isolated_test=False):
    schema, address, privileged, owner = conn.execute("""SELECT current_schema(),inet_server_addr()::text,
        r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls,
        pg_has_role(current_user,c.relowner,'MEMBER')
        FROM pg_roles r CROSS JOIN pg_class c WHERE r.rolname=current_user
        AND c.oid='processing_stage_heads'::regclass""").fetchone()
    if isolated_test:
        if not re.fullmatch(r'test_phase5a_[0-9a-f]{32}', schema or '') or not address or not ipaddress.ip_interface(address).ip.is_loopback:
            raise PermissionError('Isolated execution requires the established local temporary schema')
        return
    if (os.getenv('DATARISE_PROCESSING_DEPLOYMENT_APPROVED') != 'true'
        or os.getenv('DATARISE_PROCESSING_READERS') != 'true' or privileged or owner):
        raise PermissionError('Runtime security/deployment approval is incomplete')
    direct = conn.execute("""SELECT bool_or(has_table_privilege(current_user,n,'INSERT,UPDATE,DELETE')
        OR has_any_column_privilege(current_user,n,'INSERT,UPDATE'))
        FROM unnest(ARRAY['processing_stage_heads','processing_stage_receipts','processing_source_objects',
            'processing_admission_pins','processing_dependency_epochs','processing_dispatch_outbox']) AS n""").fetchone()[0]
    if direct:
        raise PermissionError('Runtime roles cannot directly alter publication authority')
    if conn.execute("""SELECT has_column_privilege(current_user,'processing_artifact_candidates','status','UPDATE')
        OR has_column_privilege(current_user,'processing_artifact_candidates','verification_report','UPDATE')
        OR has_column_privilege(current_user,'processing_artifact_candidates','status','INSERT')""").fetchone()[0]:
        raise PermissionError('Candidate verification requires the restricted verifier operation')
    if conn.execute("""SELECT EXISTS(SELECT 1 FROM pg_proc WHERE pronamespace=current_schema()::regnamespace
        AND proname LIKE 'task20_%' AND prosecdef AND pg_has_role(current_user,proowner,'MEMBER'))""").fetchone()[0]:
        raise PermissionError('Runtime cannot inherit authority function ownership')
    operation = {'worker':'task20_publish(uuid,uuid,uuid)', 'verifier':'task20_verify(uuid,uuid,uuid,jsonb)',
                 'dispatch':'task20_claim_dispatch()', 'api':'task20_admit(bigint,bigint,text,text)'}[purpose]
    if not conn.execute('SELECT has_function_privilege(current_user,%s,\'EXECUTE\')', (operation,)).fetchone()[0]:
        raise PermissionError('Runtime lacks its narrowly scoped operation')
    if purpose != 'verifier' and conn.execute("SELECT has_function_privilege(current_user,'task20_verify(uuid,uuid,uuid,jsonb)','EXECUTE')").fetchone()[0]:
        raise PermissionError('Verifier authority must be isolated from other runtimes')
    if purpose != 'worker' and conn.execute("SELECT has_function_privilege(current_user,'task20_publish(uuid,uuid,uuid)','EXECUTE')").fetchone()[0]:
        raise PermissionError('Publication authority must be isolated from other runtimes')
