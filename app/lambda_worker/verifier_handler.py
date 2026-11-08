
"""Fail-closed entry point for the trusted artifact verifier.

This Lambda must run with a verifier-only database identity.
It must not share database credentials with the processing worker.
"""

import logging
from uuid import UUID
from app.lambda_worker.verifier_runtime import build_verifier
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def parse_candidate(event):
    if not isinstance(event, dict) or set(event) != {"candidate_id"}:
        raise ValueError("Expected only candidate_id")

    raw_id = event["candidate_id"]

    if not isinstance(raw_id, str):
        raise ValueError("Candidate ID must be a UUID string")

    candidate_id = UUID(raw_id)

    if str(candidate_id) != raw_id:
        raise ValueError("Candidate ID must be a canonical UUID")
    return candidate_id


def handle_verification(event, verifier):
    """Verify one registered candidate and acknowledge only on success."""
    candidate_id = parse_candidate(event)

    if not callable(verifier):
        raise TypeError("Verifier service is required")

    # The verifier must raise if verification or database persistence fails.
    verifier(candidate_id)

    return {
        "verified": True,
        "candidate_id": str(candidate_id),
    }


def lambda_handler(event, context):
    """AWS entry point; fail closed until runtime wiring is approved."""

    phase = 'REQUEST'
    try:
        parse_candidate(event)  # Reject malformed requests before SDK/secret access.
        clock = getattr(context, 'get_remaining_time_in_millis', None)
        if context is not None and not callable(clock):
            raise ValueError('Invocation clock is required')
        phase = 'BOOTSTRAP'
        verifier = build_verifier(remaining_time_ms=clock) if clock else build_verifier()
        phase = 'VERIFY'
        result = handle_verification(event, verifier)
        logger.info('Artifact verification complete')
        return result
    except PermissionError:
        logger.warning("Artifact verification refused (phase=%s)", phase)
        raise PermissionError("Verifier deployment is not approved") from None
    except Exception as error:
        # Do not expose candidate evidence, database errors or credentials.
        logger.warning("Artifact verification failed (phase=%s, error=%s)", phase, type(error).__name__)
        raise RuntimeError("Verification failed") from None
