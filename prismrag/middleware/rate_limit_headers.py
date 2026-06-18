"""PrismRAG — X-RateLimit-* response headers.

Adds standard rate-limit headers to every API response so clients can
implement back-off logic without waiting for a 429.

Headers emitted:
  X-RateLimit-Limit      — requests allowed per window (from plan)
  X-RateLimit-Remaining  — estimated requests remaining this minute
  X-RateLimit-Reset      — Unix timestamp when the window resets
  X-RateLimit-Plan       — caller's plan name (free/starter/professional/enterprise)
  Retry-After            — only on 429 responses, seconds until reset

Window = 60 seconds (per-minute sliding window matching Redis quota).
"""
from __future__ import annotations

import math
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

# Per-plan requests-per-minute defaults (overridden by plan DB values where available)
_PLAN_RPM: dict[str, int] = {
    "free":         30,
    "starter":      120,
    "professional": 600,
    "enterprise":   0,    # 0 = unlimited (no limit header emitted)
}
_DEFAULT_RPM = 60
_WINDOW = 60  # seconds


class RateLimitHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        plan, rpm, remaining = _get_rate_info(request)
        if rpm == 0:
            return response  # enterprise unlimited — skip headers

        reset_ts = math.ceil(time.time() / _WINDOW) * _WINDOW

        response.headers["X-RateLimit-Limit"]     = str(rpm)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        response.headers["X-RateLimit-Reset"]      = str(reset_ts)
        response.headers["X-RateLimit-Plan"]       = plan

        if response.status_code == 429:
            response.headers["Retry-After"] = str(max(1, reset_ts - int(time.time())))

        return response


def _get_rate_info(request: Request) -> tuple[str, int, int]:
    """Return (plan, rpm_limit, estimated_remaining)."""
    plan = "free"
    user_id = None

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if not token.startswith("prk_"):
            try:
                from prismrag.auth.auth import decode_jwt
                claims = decode_jwt(token)
                plan = claims.get("plan", "free")
                user_id = claims.get("sub")
            except Exception:
                pass

    rpm = _PLAN_RPM.get(plan, _DEFAULT_RPM)
    if rpm == 0:
        return plan, 0, 0

    remaining = rpm  # default: full window available
    if user_id:
        try:
            remaining = _redis_remaining(user_id, rpm)
        except Exception:
            pass

    return plan, rpm, remaining


def _redis_remaining(user_id: str, rpm: int) -> int:
    """Estimate remaining requests in the current window from Redis."""
    url = __import__("os").getenv("REDIS_URL") or __import__("os").getenv("PRISMRAG_REDIS_URL")
    if not url:
        return rpm

    import redis as _redis_lib
    r = _redis_lib.from_url(url, decode_responses=True, socket_timeout=1)
    window_key = math.floor(time.time() / _WINDOW)
    key = f"rl:{user_id}:{window_key}"
    used = int(r.get(key) or 0)
    return max(0, rpm - used)
