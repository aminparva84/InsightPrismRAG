"""
PrismRAG — Quota enforcement and usage metering.

Three-layer design:
  1. Redis sliding-window rate limiter  (requests / minute per user)
  2. Redis monthly counter              (hot read, O(1))
  3. Postgres usage_event               (durable billing record, written async)

Redis is optional: if REDIS_URL is not set the system falls back to
Postgres-only checks (slightly slower but fully functional).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Literal

from fastapi import Depends, HTTPException

from prismrag.auth.auth import get_current_user

# ── Event types ───────────────────────────────────────────────────────────────
EventType = Literal[
    "search",          # 1 unit = 1 search query
    "ingest_chunk",    # 1 unit = 1 chunk written
    "mlp_train",       # 1 unit = 1 training run
    "bridge_create",   # 1 unit = 1 bridge vector
]

# ── Per-plan limits (loaded from DB via prismrag.plans) ───────────────────────
from prismrag.plans import get_plan_limits as _get_plan_limits


def PLAN_LIMITS(plan: str | None = None) -> dict:
    """Backward-compatible accessor. Pass plan name or use get_plan_limits() directly."""
    if plan is None:
        from prismrag.plans import get_all_plans
        return get_all_plans()
    return _get_plan_limits(plan)


def _limits_for(plan: str) -> dict:
    return _get_plan_limits(plan)

# ── Overage prices (USD per 1 000 units) ─────────────────────────────────────
OVERAGE_PRICE_PER_1K: dict[str, float] = {
    "ingest_chunk": 0.80,
    "search":       1.50,
    "mlp_train":    5.00,   # per run, not per-1K
    "bridge_create": 2.00,
}

# ── Redis client (optional) ───────────────────────────────────────────────────
_redis = None
_redis_lock = threading.Lock()


def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    with _redis_lock:
        if _redis is None:
            try:
                import redis as _r
                _redis = _r.from_url(url, decode_responses=True, socket_timeout=0.5)
                _redis.ping()
            except Exception:
                _redis = None  # degrade gracefully
    return _redis


# ── Redis rate limit (sliding window, per-minute) ─────────────────────────────

def _redis_rate_limit(user_id: str, plan: str) -> None:
    """Raises 429 if user exceeds req/min limit. No-op if Redis unavailable."""
    limit = _limits_for(plan)["req_per_min"]
    if limit == 0:
        return  # enterprise: no rate limit

    r = _get_redis()
    if r is None:
        return  # no Redis — skip (Postgres quota check still runs)

    now = time.time()
    window_start = now - 60
    key = f"rl:{user_id}"
    try:
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, 120)
        results = pipe.execute()
        count = results[2]
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {limit} requests/minute. "
                       f"Upgrade your plan or retry after a minute.",
                headers={"Retry-After": "60"},
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Redis error → degrade gracefully


# ── Redis monthly counter ─────────────────────────────────────────────────────

def _redis_monthly_used(user_id: str, event_type: EventType) -> int | None:
    """Returns current monthly count from Redis, or None if unavailable."""
    r = _get_redis()
    if r is None:
        return None
    import datetime
    ym = datetime.datetime.utcnow().strftime("%Y-%m")
    key = f"quota:{user_id}:{event_type}:{ym}"
    try:
        val = r.get(key)
        return int(val) if val else 0
    except Exception:
        return None


def _redis_increment(user_id: str, event_type: EventType, units: int) -> None:
    """Increment Redis monthly counter. Fire-and-forget."""
    r = _get_redis()
    if r is None:
        return
    import datetime
    ym = datetime.datetime.utcnow().strftime("%Y-%m")
    key = f"quota:{user_id}:{event_type}:{ym}"
    try:
        pipe = r.pipeline()
        pipe.incrby(key, units)
        pipe.expire(key, 35 * 86400)  # 35-day TTL
        pipe.execute()
    except Exception:
        pass


# ── Postgres monthly usage (fallback when Redis unavailable) ──────────────────

def _pg_monthly_used(user_id: str, event_type: EventType) -> int:
    from prismrag.db import get_conn, release_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(SUM(units), 0)
            FROM prismrag.usage_event
            WHERE user_id = %s
              AND event_type = %s
              AND date_trunc('month', created_at) = date_trunc('month', now())
            """,
            (user_id, event_type),
        )
        return int(cur.fetchone()[0])
    finally:
        release_conn(conn)


# ── Async usage event write ───────────────────────────────────────────────────

def _write_usage_event_bg(
    user_id: str,
    tenant_id: str | None,
    event_type: EventType,
    units: int,
) -> None:
    """Write to usage_event in a background thread — never blocks the response."""
    def _write():
        from prismrag.db import get_conn, release_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO prismrag.usage_event
                    (user_id, tenant_id, event_type, units)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, tenant_id, event_type, units),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            release_conn(conn)

    threading.Thread(target=_write, daemon=True).start()


# ── Public API ────────────────────────────────────────────────────────────────

def check_feature(plan: str, feature: str) -> None:
    """Raise 403 if plan doesn't include the feature."""
    limits = _limits_for(plan)
    if not limits.get(feature, False):
        raise HTTPException(
            status_code=403,
            detail=f"Feature '{feature}' requires a higher plan. "
                   f"Your plan: {plan}. Upgrade at /dashboard.html#billing",
        )


def check_and_record(
    user: dict,
    event_type: EventType,
    units: int = 1,
    tenant_id: str | None = None,
) -> None:
    """
    Full metering call:
      1. Rate limit check (Redis)
      2. Monthly quota check (Redis → Postgres fallback)
      3. Increment Redis counter
      4. Write Postgres usage_event (background)

    Raises HTTPException if over limit.
    For overage-allowed plans (enterprise), always passes but records.
    """
    user_id = user["id"]
    plan    = user.get("plan", "free")
    limits  = _limits_for(plan)

    # 1. Rate limit
    _redis_rate_limit(user_id, plan)

    # 2. Monthly quota — map event_type to the right limit key
    limit_key = "monthly_searches" if event_type == "search" else "monthly_chunks"
    monthly_limit = limits[limit_key]

    if monthly_limit > 0:  # 0 = unlimited
        # Try Redis first, fall back to Postgres
        current = _redis_monthly_used(user_id, event_type)
        if current is None:
            current = _pg_monthly_used(user_id, event_type)

        if current + units > monthly_limit:
            overage_price = OVERAGE_PRICE_PER_1K.get(event_type, 1.0)
            raise HTTPException(
                status_code=402,
                detail={
                    "error":         "quota_exceeded",
                    "event_type":    event_type,
                    "used":          current,
                    "limit":         monthly_limit,
                    "overage_price": f"${overage_price:.2f} per 1,000 units",
                    "upgrade_url":   "/dashboard.html#billing",
                },
            )

    # 3. Increment counter
    _redis_increment(user_id, event_type, units)

    # 4. Persist usage event (non-blocking)
    _write_usage_event_bg(user_id, tenant_id, event_type, units)


# ── FastAPI dependency factories ──────────────────────────────────────────────

def metered(event_type: EventType, units: int = 1, feature: str | None = None):
    """
    FastAPI Depends factory.

    Usage:
        @router.post("/search")
        async def search(
            req: SearchRequest,
            user: dict = Depends(metered("search"))
        ):
            ...
    """
    def _dep(user: dict = Depends(get_current_user)) -> dict:
        plan = user.get("plan", "free")
        if feature:
            check_feature(plan, feature)
        check_and_record(user, event_type, units)
        return user
    return _dep


def metered_ingest(units_from_body: int = 1):
    """Special case: ingest units come from the job body, not a fixed count.
    Use this in the job handler directly after computing chunk count."""
    def _dep(user: dict = Depends(get_current_user)) -> dict:
        return user
    return _dep
