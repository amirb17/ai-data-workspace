from fastapi import HTTPException
from app.services.processing_context_service import StaleRuleVersion


def public_call(operation):
    try:
        return operation()
    except PermissionError as exc:
        raise HTTPException(403, "Processing context access denied") from exc
    except LookupError as exc:
        raise HTTPException(404, "Processing context not found") from exc
    except StaleRuleVersion as exc:
        raise HTTPException(409, "Rule context changed; reload before continuing") from exc
    except ValueError as exc:
        raise HTTPException(400, "Invalid processing context or rule answer") from exc
    except RuntimeError as exc:
        raise HTTPException(409, "Processing is unavailable or already active; reload and retry") from exc
    except Exception as exc:
        raise HTTPException(503, "Processing service unavailable; reload and retry") from exc
