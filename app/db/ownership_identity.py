"""Legacy signature compatibility, never an authorization or principal alias."""


def source_membership(conn, workspace, rows):
    """Freeze the legacy v1 owner slot to resource provenance, not its live owner.

    Auth always checks the original rows separately. Exact event scope preserves
    old signatures byte-for-byte without changing history or masking data drift.
    New datasets are not silently included in an old adoption event.
    """
    exists = conn.execute("SELECT to_regclass('ownership_adoption_events')").fetchone()[0]
    origins = {}
    if exists:
        events = conn.execute('''SELECT source_principal,resource_scope FROM ownership_adoption_events
            WHERE operation='ADOPT' AND resource_scope ? %s ORDER BY executed_at,adoption_id''',
            (str(workspace),)).fetchall()
        for principal, scope in events:
            for dataset in scope[str(workspace)]:
                origins.setdefault(dataset, principal)
    result = []
    for row in rows:
        item = {key: row[key] for key in ('dataset_id', 'dataset_name', 'owner', 'status',
                                         'current_state_id', 'current_profile_id', 'profile_status')}
        item['owner'] = origins.get(row['dataset_id'], row['owner'])
        result.append(item)
    return result
