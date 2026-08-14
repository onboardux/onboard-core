"""Project-level views that belong to no app."""


def health_check(request):
    """Liveness probe. Serves any method, which is what `ANY` records."""
    return
