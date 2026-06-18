"""PrismRAG — IP Allowlist enforcement middleware.

When an organization has ip_allowlist_enabled=TRUE, all API requests from
that org's members must originate from a CIDR in the allowlist.

Non-authenticated requests and requests from orgs without allowlist enabled
pass through unchanged.

CIDR entries are cached in Redis (or in-process) for 60 seconds to avoid
a DB hit on every request.
"""
from __future__ import annotations

import ipaddress
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

_CACHE_TTL = 60  # seconds


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self._local_cache: dict[str, tuple[float, list]] = {}

    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return await call_next(request)

        token = auth[7:].strip()
        org_id = None

        if token.startswith("prk_"):
            org_id = _org_id_from_api_key(token)
        else:
            try:
                from prismrag.auth.auth import decode_jwt
                claims = decode_jwt(token)
                user_id = claims.get("sub")
                if user_id:
                    org_id = _org_id_from_user(user_id)
            except Exception:
                pass

        if not org_id:
            return await call_next(request)

        cidrs = self._get_allowlist(org_id)
        if cidrs is None:
            return await call_next(request)  # allowlist disabled for this org

        client_ip = _client_ip(request)
        if not _ip_allowed(client_ip, cidrs):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": f"IP {client_ip} is not in your organization's allowlist.",
                    "code": "ip_not_allowed",
                },
            )

        return await call_next(request)

    def _get_allowlist(self, org_id: str) -> list[str] | None:
        """Return CIDR list if allowlist is enabled, None if disabled. Cached."""
        cached_at, data = self._local_cache.get(org_id, (0, None))
        if time.time() - cached_at < _CACHE_TTL:
            return data

        result = _load_allowlist(org_id)
        self._local_cache[org_id] = (time.time(), result)
        return result


def _load_allowlist(org_id: str) -> list[str] | None:
    try:
        from prismrag.db import get_conn, release_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT ip_allowlist_enabled FROM prismrag.organization WHERE id = %s",
                (org_id,),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                return None
            cur.execute(
                "SELECT cidr FROM prismrag.ip_allowlist WHERE organization_id = %s",
                (org_id,),
            )
            return [r[0] for r in cur.fetchall()]
        finally:
            release_conn(conn)
    except Exception:
        return None


def _ip_allowed(ip_str: str, cidrs: list[str]) -> bool:
    if not cidrs:
        return True
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in ipaddress.ip_network(c, strict=False) for c in cidrs)
    except ValueError:
        return False


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def _org_id_from_user(user_id: str) -> str | None:
    try:
        from prismrag.db import get_conn, release_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT organization_id::text FROM prismrag.user_account WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None
        finally:
            release_conn(conn)
    except Exception:
        return None


def _org_id_from_api_key(token: str) -> str | None:
    import hashlib
    try:
        from prismrag.db import get_conn, release_conn
        key_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT u.organization_id::text
                FROM prismrag.api_key ak
                JOIN prismrag.user_account u ON u.id = ak.user_id
                WHERE ak.key_hash = %s AND ak.is_active
                """,
                (key_hash,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None
        finally:
            release_conn(conn)
    except Exception:
        return None
