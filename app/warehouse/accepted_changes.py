"""Versioned bounded accepted evidence. No upload, provider, or clock identities."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.warehouse.keys import canonical_value
from app.warehouse.scd_hashing import digest as _digest


def digest(value):
    """V1 canonical mapping order around the existing exact UTF-8 hash helper.

    Scalars in business payloads are already type-tagged by canonical_value.
    Metadata objects must also be independent of Python/JSONB insertion order.
    """
    def ordered(v):
        if isinstance(v, dict):
            if not all(isinstance(k, str) for k in v):
                raise ValueError('Canonical metadata requires string field names')
            return {k: ordered(v[k]) for k in sorted(v)}
        if isinstance(v, (list, tuple)):
            return [ordered(x) for x in v]
        return v
    return _digest(ordered(value))


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class SourcePin(Contract):
    workspace_id: int = Field(gt=0)
    dataset_id: int = Field(gt=0)
    dataset_version_id: int = Field(gt=0)
    policy_id: int = Field(gt=0)
    rule_version: int = Field(gt=0)
    rule_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    application_id: int = Field(gt=0)
    upload_id: int = Field(gt=0)
    source_dq_run_id: int = Field(gt=0)
    state_id: int = Field(gt=0)
    previous_state_id: int | None = Field(default=None, gt=0)
    artifact_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    strategy: Literal['APPEND', 'UPSERT', 'SNAPSHOT']
    coverage: Literal['COMPLETE', 'PARTIAL'] | None = None
    snapshot_outcome: str | None = Field(default=None, max_length=40)
    snapshot_effective_at: datetime | None = None
    delivery_kind: Literal['NORMAL', 'CORRECTION', 'BACKFILL'] | None = None
    accepted_at: datetime

    @model_validator(mode='after')
    def valid_time(self):
        canonical_value(self.accepted_at)
        if self.snapshot_effective_at is not None:
            canonical_value(self.snapshot_effective_at)
        if (self.strategy == 'SNAPSHOT') != (self.coverage is not None):
            raise ValueError('Snapshot authority required only for SNAPSHOT')
        return self

    def wire(self):
        # Audit acceptance time is pinned, but never an event identity input.
        return self.model_dump(mode='json')


def projection(configuration):
    """Only explicitly reviewed model roles enter trusted warehouse evidence."""
    if configuration.model_type == 'DIMENSION':
        names = (
            list(configuration.business_keys)
            + list(configuration.tracked_attributes)
            + list(configuration.nonhistorical_attributes)
        )
        time = configuration.effective_time
    else:
        names = (
            list(configuration.grain.identity_columns)
            + list(configuration.measures)
            + list(configuration.attributes)
        )
        time = configuration.event_time

    if time:
        names.append(time.column)

    return tuple(sorted(set(names)))

def payload(row, columns):
    if not columns or len(columns) > 128 or len(set(columns)) != len(columns):
        raise ValueError('Bounded distinct projection required')
    result = [[c, canonical_value(row[c])] for c in sorted(columns)]
    # Bound the encoded representation, not just the number of fields.
    import json
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 65536:
        raise ValueError('Projected event exceeds 64 KiB')
    return result


class AcceptedEvent(Contract):
    version: Literal[1] = 1
    event_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    evidence_key: str = Field(min_length=1, max_length=255)
    outcome: Literal['INSERTED', 'UPDATED', 'UNCHANGED', 'REACTIVATED', 'DEACTIVATED']
    payload: tuple = Field(max_length=128)
    checksum: str = Field(pattern=r'^[a-f0-9]{64}$')


def event(pin, evidence_key, outcome, row, columns):
    # evidence_key is an existing immutable ledger key/row locator in the pinned
    # accepted artifact, never a row number allocated by warehouse execution.
    identity = digest(['datarise-accepted-event', 1, pin.workspace_id, pin.dataset_id,
                       pin.application_id, pin.state_id, evidence_key, outcome])
    body = payload(row, columns)
    checksum = digest(['datarise-accepted-payload', 1, outcome, body])
    return AcceptedEvent(event_id=identity, evidence_key=evidence_key, outcome=outcome,
                         payload=tuple((n, tuple(v)) for n, v in body), checksum=checksum)


def manifest(pin, mapping_id, model_version_id, configuration_sha256, events, position):
    import re
    if (any(type(x) is not int or x < 1 for x in (position, mapping_id, model_version_id))
            or not re.fullmatch(r'[a-f0-9]{64}', configuration_sha256) or len(events) > 1000):
        raise ValueError('Bounded manifest and positive sequence required')
    ordered = sorted(events, key=lambda e: e.event_id)
    if len({e.event_id for e in ordered}) != len(ordered):
        raise ValueError('Duplicate event identity')
    content = [e.model_dump(mode='json') for e in ordered]
    checksum = digest(['datarise-accepted-manifest', 1, pin.wire(), mapping_id,
                       model_version_id, configuration_sha256, position, content])
    identity = digest(['datarise-accepted-manifest-id', 1, pin.workspace_id,
                       mapping_id, model_version_id, pin.state_id])
    return dict(manifest_id=identity, checksum=checksum, position=position, pin=pin.wire(),
                events=content, event_count=len(content), material_count=sum(e.outcome != 'UNCHANGED' for e in ordered),
                source_mapping_id=mapping_id, model_version_id=model_version_id,
                configuration_sha256=configuration_sha256, contract_version=1)


def verify_manifest(body):
    """Fail closed on a damaged projected manifest before reference execution."""
    if body['contract_version'] != 1 or not 0 <= len(body['events']) <= 1000:
        raise ValueError('Unsupported manifest contract or bound')
    import json
    pin = SourcePin.model_validate_json(json.dumps(body['pin']))
    events = []
    for row in body['events']:
        e = AcceptedEvent.model_validate_json(json.dumps(row))
        expected_id = digest(['datarise-accepted-event', 1, pin.workspace_id, pin.dataset_id,
                              pin.application_id, pin.state_id, e.evidence_key, e.outcome])
        if e.event_id != expected_id or e.checksum != digest(['datarise-accepted-payload', 1, e.outcome, e.payload]):
            raise ValueError('Accepted event identity/payload conflict')
        events.append(e)
    expected = manifest(pin, body['source_mapping_id'], body['model_version_id'],
                        body['configuration_sha256'], events, body['position'])
    if body != expected:
        raise ValueError('Accepted manifest checksum/content conflict')
    return body
