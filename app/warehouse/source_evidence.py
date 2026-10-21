"""Read immutable trusted-state artifacts, not Silver completion or latest files."""
import hashlib
import json

from app.processing.append_engine import LINEAGE, scalar, append_rows
from app.processing.upsert_engine import upsert_rows
from app.processing.snapshot_engine import snapshot_rows
from app.warehouse.accepted_changes import SourcePin, event
from app.warehouse.reliability import ProtocolError, Decision


MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_ROWS = 1000


def checked_json(store, key, checksum):
    body = store.read(key, max_bytes=MAX_ARTIFACT_BYTES)
    if hashlib.sha256(body).hexdigest() != checksum:
        raise ProtocolError(Decision.SOURCE_DRIFT)
    return json.loads(body)


def classify_bootstrap(*, exact_chain, artifacts_verified, current_state_verified):
    if exact_chain and artifacts_verified:
        return 'EXACT_ACCEPTED_EVIDENCE'
    if current_state_verified:
        return 'CURRENT_STATE_SEED_REQUIRES_INTENT'
    return 'EXPLICIT_REBUILD_REQUIRED'


def extract(store, state, application, policy, delivery, previous, columns, workspace_id, rule_hash):
    """Caller supplies DB-verified published rows and historical pinned policy.

    Recompute with the SAME existing engine and compare immutable artifacts and
    outcomes. This authenticates classification, not a new source acceptance path.
    All artifact reads are bounded before Parquet decoding. No writes.
    """
    import io
    import pandas as pd
    if (state['status'] != 'PUBLISHED' or application['status'] != 'SUCCESS'
            or application['result_state_id'] != state['state_id']):
        raise ProtocolError(Decision.SOURCE_DRIFT)
    sm = checked_json(store, state['manifest_key'], state['manifest_sha256'])
    dm = checked_json(store, delivery['manifest_key'], delivery['manifest_sha256'])
    for field in ('application_id', 'dataset_id', 'dataset_version_id', 'policy_id', 'upload_request_id'):
        if sm[field] != application[field] or dm[field] != application[field]:
            raise ProtocolError(Decision.SOURCE_DRIFT)
    if (dm['silver_key'] != delivery['silver_key'] or dm['silver_sha256'] != delivery['silver_sha256']
            or dm['policy'] != json.loads(store.json(policy))
            or sm['previous_state_id'] != state['previous_state_id']):
        raise ProtocolError(Decision.SOURCE_DRIFT)

    def frame(key, checksum):
        body = store.read(key, max_bytes=MAX_ARTIFACT_BYTES)
        if hashlib.sha256(body).hexdigest() != checksum:
            raise ProtocolError(Decision.SOURCE_DRIFT)
        result = pd.read_parquet(io.BytesIO(body))
        if len(result) > MAX_ROWS:
            raise ValueError('Bounded V1 source extraction requires explicit larger-input adapter')
        return result

    incoming = frame(delivery['silver_key'], delivery['silver_sha256'])
    candidate = frame(sm['data_key'], sm['data_sha256'])
    if len(incoming) != application['valid_rows'] or len(candidate) != state['row_count']:
        raise ProtocolError(Decision.SOURCE_DRIFT)
    current = None
    if previous:
        pm = checked_json(store, previous['manifest_key'], previous['manifest_sha256'])
        current = frame(pm['data_key'], pm['data_sha256'])
    if current is None:
        current = candidate.iloc[0:0].copy()
    mode = policy['load_strategy']
    app = dict(application)
    if mode == 'SNAPSHOT':
        app['previous_snapshot_boundary_at'] = previous.get('snapshot_boundary_at') if previous else None
    engine = {'APPEND': append_rows, 'UPSERT': upsert_rows, 'SNAPSHOT': snapshot_rows}[mode]
    expected, counts, ledger = engine(current, incoming, policy, app)
    try:
        pd.testing.assert_frame_equal(expected.reset_index(drop=True), candidate.reset_index(drop=True),
                                      check_dtype=False, check_exact=True)
    except AssertionError:
        # Pandas diagnostics can contain business values. Expose only a safe
        # protocol code to a future caller/logger.
        raise ProtocolError(Decision.SOURCE_DRIFT) from None
    if counts != sm['counts']:
        raise ProtocolError(Decision.SOURCE_DRIFT)
    outcomes = checked_json(store, sm['outcomes_key'], sm['outcomes_sha256'])
    expected_outcomes = {'rejected': ledger} if mode == 'APPEND' else {
        'ledger': ledger, 'rejected': [r for r in ledger if r['outcome'] == 'REJECTED']}
    if outcomes != json.loads(store.json(expected_outcomes)):
        raise ProtocolError(Decision.SOURCE_DRIFT)
    pin = SourcePin(workspace_id=workspace_id, dataset_id=application['dataset_id'],
                    dataset_version_id=application['dataset_version_id'], policy_id=application['policy_id'],
                    rule_version=application['applied_rule_version'], rule_sha256=rule_hash,
                    application_id=application['application_id'], upload_id=application['upload_request_id'],
                    source_dq_run_id=application['source_dq_run_id'],
                    state_id=state['state_id'], previous_state_id=state['previous_state_id'],
                    artifact_sha256=state['manifest_sha256'], strategy=mode,
                    coverage=application.get('snapshot_coverage'), snapshot_outcome=application.get('snapshot_outcome'),
                    snapshot_effective_at=application.get('snapshot_effective_at'), delivery_kind=application.get('delivery_kind'),
                    accepted_at=state['published_at'])
    events = []

    def projected(row):
        return {c: scalar(row[c]) for c in columns}

    if mode == 'APPEND':
        # APPEND stores no accepted row ledger. Verified candidate prefix equality
        # proves the immutable suffix is precisely its accepted inserts. An ordinal
        # here is a locator into those pinned bytes, not a generated warehouse key.
        for ordinal in range(len(current), len(candidate)):
            events.append(event(pin, f'state-record:{ordinal}', 'INSERTED', projected(candidate.iloc[ordinal]), columns))
    else:
        for item in ledger:
            if item['outcome'] in ('INSERTED', 'UPDATED', 'UNCHANGED', 'REACTIVATED'):
                row = incoming.iloc[item['silver_row']]
                events.append(event(pin, 'ledger-key:' + item['key_hash'], item['outcome'], projected(row), columns))
        if mode == 'SNAPSHOT':
            for row in candidate.to_dict('records'):
                if (not row['_datarise_active'] and scalar(row['_datarise_deactivated_application_id']) == pin.application_id):
                    from app.processing.upsert_engine import identity
                    key = identity(row, [c['name'] for c in policy['schema_columns']], policy['business_keys'])[1]
                    events.append(event(pin, 'deactivation-key:' + key, 'DEACTIVATED', projected(row), columns))
    return pin, tuple(events)
