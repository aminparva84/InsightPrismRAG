"""PrismRAG — API versioning helpers."""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# Paths that have a v1 counterpart (legacy /api/* → /api/v1/*)
_LEGACY_PREFIXES = (
    "/api/prismrag",
    "/api/auth",
    "/api/billing",
    "/api/deliberation",
)


class LegacyApiMiddleware(BaseHTTPMiddleware):
    """
    Rewrite unversioned API paths to /api/v1/... for backward compatibility.
    Example: /api/prismrag/health → /api/v1/prismrag/health
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        path = request.scope.get("path", "")
        if path.startswith("/api/v1/"):
            return await call_next(request)
        for prefix in _LEGACY_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                request.scope["path"] = path.replace("/api/", "/api/v1/", 1)
                break
        return await call_next(request)
