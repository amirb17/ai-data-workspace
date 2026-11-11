"""414E3 exact server policy comparison; never a provider execution contract."""
from dataclasses import asdict, dataclass

from app.warehouse.query_recovery_attestation import (
    LookupPolicy, MetadataReport, digest, verify_metadata,
)


@dataclass(frozen=True)
class ProviderPolicy:
    project: str
    creator_email: str
    revision: str
    location: str = 'asia-south1'
    timeout_ms: int = 60000
    maximum_bytes: int = 256 * 1024 * 1024
    principal_subject: str | None = None

    def __post_init__(self):
        # Reuse the existing narrow development/Mumbai creator and scope grammar.
        LookupPolicy(self.project, self.location, self.creator_email, self.revision,
                     self.timeout_ms, self.principal_subject)
        if type(self.maximum_bytes) is not int or not 1 <= self.maximum_bytes <= 1024**3:
            raise ValueError('Bounded server provider byte policy required')

    @property
    def sha256(self) -> str:
        return digest(asdict(self))


def verify_exact_metadata(raw, core, lookup_policy, expected) -> MetadataReport:
    report = verify_metadata(raw, core, lookup_policy)
    if report.verdict != 'VERIFIED_METADATA':
        return report
    config = raw['configuration']
    query = config['query']
    # E2 already checked numeric types, unknown settings and exact SQL/table scope.
    checks = (
        (config.get('dryRun') is False, 'MISSING_EXACT_CONFIGURATION'),
        (config['labels'] == expected['labels'], 'EXACT_LABEL_POLICY_MISMATCH'),
        (int(query['maximumBytesBilled']) == expected['maximum_bytes_billed'], 'EXACT_BILLING_POLICY_MISMATCH'),
        (int(config['jobTimeoutMs']) == expected['job_timeout_ms'], 'EXACT_TIMEOUT_POLICY_MISMATCH'),
    )
    for matched, reason in checks:
        if not matched:
            return MetadataReport('UNCERTAIN', 'REJECTED', reason, report.payload_sha256)
    return report
