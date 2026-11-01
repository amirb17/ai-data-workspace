"""Artifact verification prior to database publication.

This module does not authorize real processing or access AWS directly.
"""

from app.processing_worker.artifact_contract import verify_candidate


def verify_stage_artifacts(
    manifest_bytes,
    *,
    manifest_sha256,
    intent_id,
    candidate_id,
    stage,
    input_fingerprint,
    expected_roles,
    read_artifact,
    max_artifact_bytes,
):
    """Validate artifact bytes against a trusted stage intent.

    This function returns verification evidence only.
    It does not update PostgreSQL or publish an artifact.
    """
    return verify_candidate(
        manifest_bytes,
        manifest_sha256=manifest_sha256,
        intent_id=intent_id,
        candidate_id=candidate_id,
        stage=stage,
        input_fingerprint=input_fingerprint,
        expected_roles=expected_roles,
        read_artifact=read_artifact,
        max_artifact_bytes=max_artifact_bytes,
    )