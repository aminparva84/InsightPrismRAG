"""PrismRAG — MFA enforcement middleware.

When an organization sets mfa_required=TRUE, any JWT-authenticated request
from a user who has not yet enabled MFA is rejected with 403 so they are
forced through the MFA enrolment flow before accessing any API resource.

Paths that are always exempt (login, MFA enrol/verify, health):
  - /api/v1/auth/*
  - /api/v1/mfa/*
  - /health, /metrics, /docs, /redoc, /openapi.json
  - /static/*, / (static frontend)
"""
from __future__ import annotations

import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

_EXEMPT_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/mfa/",
    "/api/v1/scim/",
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/static/",
)

_MFA_ENFORCEMENT_ENABLED = os.getenv("PRISMRAG_MFA_ENFORCEMENT", "true").lower() in ("1", "true", "yes")


class MFAEnforcementMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        if not _MFA_ENFORCEMENT_ENABLED:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES) or path in ("/", ""):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return await call_next(request)

        token = auth[7:].strip()
        if not token.startswith("prk_"):
            # JWT bearer — check MFA requirement
            try:
                from prismrag.auth.auth import decode_jwt
                claims = decode_jwt(token)
                user_id = claims.get("sub")
                if user_id:
                    result = _check_mfa_required(user_id)
                    if result:
                        return JSONResponse(
                            status_code=403,
                            content={
                                "detail": result,
                                "code": "mfa_required",
                                "enrolUrl": "/api/v1/mfa/enrol",
                            },
                        )
            except Exception:
                pass  # Auth errors handled downstream

        return await call_next(request)


def _check_mfa_required(user_id: str) -> str | None:
    """Return error message if MFA is required but not enabled, else None."""
    try:
        from prismrag.db import get_conn, release_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT u.mfa_enabled, o.mfa_required
                FROM prismrag.user_account u
                LEFT JOIN prismrag.organization o ON o.id = u.organization_id
                WHERE u.id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
        finally:
            release_conn(conn)

        if not row:
            return None
        mfa_enabled, mfa_required = row
        if mfa_required and not mfa_enabled:
            return "Your organization requires MFA. Please enrol at /api/v1/mfa/enrol."
    except Exception:
        pass
    return None
