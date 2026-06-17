"""
PrismRAG — Request/response audit middleware.

Every API call is logged to prismrag.api_request_log:
  - user identity (from JWT/API key)
  - endpoint, method, status code, latency
  - sanitized request body (passwords/keys stripped, body truncated at 8 KB)
  - truncated response body (4 KB max — enough for debugging, not a data dump)

Write is always async (background thread) so it never adds latency to the response.
Sensitive paths (register, login) have response body suppressed.
"""
from __future__ import annotations

import json
import os
import time
import threading
import hashlib
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# Max bytes stored per field
_REQ_BODY_LIMIT  = 8_192    # 8 KB
_RESP_BODY_LIMIT = 4_096    # 4 KB

# Endpoints where we suppress the response body entirely (contains tokens/keys)
_SUPPRESS_RESP_PATHS = {
    "/api/auth/register", "/api/auth/login", "/api/auth/api-keys",
    "/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/api-keys",
}

_SKIP_PREFIXES = ("/static/", "/favicon", "/api/prismrag/health", "/api/v1/prismrag/health", "/metrics")

_STRIP_FIELDS = {"password", "password_hash", "raw_key", "key_hash", "stripe_secret_key"}

# How many days to keep logs per plan
LOG_RETENTION_DAYS: dict[str, int] = {
    "free":         7,
    "starter":      30,
    "professional": 30,
    "enterprise":   90,
}


class AuditMiddleware(BaseHTTPMiddleware):
    """Starlette middleware — wraps every request, logs to DB in background."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip non-API and health paths
        path = request.url.path
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        # Read request body (Starlette consumes the stream — we need to buffer it)
        req_body_bytes = await request.body()
        req_body_str   = _sanitize_body(req_body_bytes)

        # Identify user from Authorization header (best-effort, non-blocking)
        user_id, plan = _extract_identity(request)

        # Forward request downstream
        t0 = time.perf_counter()
        # Re-inject body so the handler can read it
        async def receive():
            return {"type": "http.request", "body": req_body_bytes, "more_body": False}
        request._receive = receive  # type: ignore[attr-defined]

        response = await call_next(request)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Capture response body (streaming — consume and re-wrap)
        resp_body_bytes = b""
        async for chunk in response.body_iterator:
            resp_body_bytes += chunk

        resp_body_str = ""
        if path not in _SUPPRESS_RESP_PATHS:
            resp_body_str = resp_body_bytes[:_RESP_BODY_LIMIT].decode("utf-8", errors="replace")

        # Rebuild response with the consumed body
        response = Response(
            content=resp_body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

        # Write log record in background — never blocks
        trace_id = getattr(request.state, "trace_id", "")
        _write_log_bg(
            user_id=user_id,
            plan=plan,
            method=request.method,
            path=path,
            query=str(request.url.query),
            status_code=response.status_code,
            latency_ms=latency_ms,
            req_body=req_body_str,
            resp_body=resp_body_str,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:200],
            trace_id=trace_id,
        )

        return response


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sanitize_body(raw: bytes) -> str:
    """Parse JSON, strip sensitive fields, truncate."""
    if not raw:
        return ""
    try:
        obj = json.loads(raw[:_REQ_BODY_LIMIT])
        if isinstance(obj, dict):
            for field in _STRIP_FIELDS:
                if field in obj:
                    obj[field] = "***"
        return json.dumps(obj, ensure_ascii=False)[:_REQ_BODY_LIMIT]
    except Exception:
        # Not JSON (file upload, etc.) — store a placeholder
        size = len(raw)
        return f"<binary {size} bytes>"


def _extract_identity(request: Request) -> tuple[str | None, str]:
    """Best-effort user extraction from Authorization header without hitting DB."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None, "anonymous"
    token = auth[7:]
    if token.startswith("prk_"):
        # API key — hash identifies the user without a DB round-trip
        key_hash = hashlib.sha256(token.encode()).hexdigest()
        return f"apikey:{key_hash[:12]}", "unknown"
    # JWT — decode without DB
    try:
        import jwt
        jwt_secret = (
            os.getenv("PRISMRAG_JWT_SECRET")
            or os.getenv("JWT_SECRET")
            or ""
        )
        if not jwt_secret:
            return None, "anonymous"
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        return payload.get("sub"), payload.get("plan", "unknown")
    except Exception:
        return None, "invalid"


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _write_log_bg(**kwargs) -> None:
    threading.Thread(target=_write_log, kwargs=kwargs, daemon=True).start()


def _write_log(**kwargs) -> None:
    try:
        from prismrag.db import get_conn, release_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO prismrag.api_request_log
                    (user_id, plan, method, path, query_string,
                     status_code, latency_ms,
                     req_body, resp_body,
                     client_ip, user_agent, trace_id)
                VALUES
                    (%(user_id)s, %(plan)s, %(method)s, %(path)s, %(query)s,
                     %(status_code)s, %(latency_ms)s,
                     %(req_body)s, %(resp_body)s,
                     %(ip)s, %(user_agent)s, %(trace_id)s)
                """,
                kwargs,
            )
            conn.commit()
        finally:
            release_conn(conn)
    except Exception:
        pass  # logging must never crash the app
