class OwnershipError(PermissionError, ValueError):
    """Authorization denial, retaining compatibility with legacy service callers."""
