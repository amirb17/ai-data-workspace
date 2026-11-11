-- 0057: harden existing query security functions without changing owners or runtime GRANTs.
-- Apply only after 0056 with explicit migration approval; B11 tests this on
-- disposable warehouse schemas. No cloud IO, no SECURITY DEFINER, no role DDL.
BEGIN;
DO $migration$
DECLARE
    target_schema TEXT := current_schema();
    names TEXT[] := ARRAY[
        'warehouse_query_attempt_registry_guard',
        'warehouse_query_attempt_publication_guard',
        'warehouse_query_attempt_reservation_link_guard',
        'warehouse_query_linked_pin_insert_guard',
        'warehouse_query_retirement_fence_guard',
        'warehouse_query_retirement_link_guard',
        'warehouse_query_dispatch_record_guard',
        'warehouse_query_dispatch_revoke_notice',
        'warehouse_query_send_intent_guard',
        'warehouse_query_send_journal_guard',
        'warehouse_query_send_initial_event_guard',
        'warehouse_query_recovery_attestation_guard',
        'warehouse_query_provider_policy_guard',
        'warehouse_query_publication_dependency_present'
    ];
    item TEXT;
    found_count INTEGER;
    func_signature TEXT;
    owner_security BOOLEAN;
BEGIN
    IF target_schema = 'public' OR target_schema LIKE 'pg_%' THEN
        RAISE EXCEPTION '0057 refuses unreviewed system/public schema';
    END IF;
    FOREACH item IN ARRAY names LOOP
        SELECT COUNT(*), MIN(format('%I.%I(%s)', n.nspname, p.proname,
                       pg_get_function_identity_arguments(p.oid))),
               bool_or(p.prosecdef)
          INTO found_count, func_signature, owner_security
          FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
         WHERE n.nspname=target_schema AND p.proname=item;
        IF found_count <> 1 OR owner_security IS DISTINCT FROM FALSE THEN
            RAISE EXCEPTION '0057 requires exactly one SECURITY INVOKER function: %', item;
        END IF;
        -- Explicit trusted schema resolution for these invoker functions.
        EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, %I, pg_temp',
                       func_signature, target_schema);
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', func_signature);
    END LOOP;
END
$migration$;
COMMIT;
