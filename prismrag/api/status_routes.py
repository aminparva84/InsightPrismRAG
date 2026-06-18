"""PrismRAG — Public status + SLA API."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/status", tags=["Status"])
status_router = router

SLA_UPTIME_TARGET = float(os.getenv("PRISMRAG_SLA_UPTIME_PCT", "99.9"))


def _check_db() -> dict:
    t0 = time.perf_counter()
    try:
        from prismrag.db import get_conn, release_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            release_conn(conn)
        ms = int((time.perf_counter() - t0) * 1000)
        return {"status": "operational", "latency_ms": ms}
    except Exception as exc:
        return {"status": "outage", "error": str(exc)[:200]}


def _check_redis() -> dict:
    try:
        from prismrag.metering.quota import _get_redis
        r = _get_redis()
        if r is None:
            return {"status": "degraded", "note": "Redis not configured (optional)"}
        r.ping()
        return {"status": "operational"}
    except Exception as exc:
        return {"status": "degraded", "error": str(exc)[:100]}


def _check_worker() -> dict:
    from prismrag.db import get_conn, release_conn
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*) FROM prismrag.job_queue
                WHERE status = 'pending' AND created_at < now() - interval '10 minutes'
                """
            )
            stale = cur.fetchone()[0]
        finally:
            release_conn(conn)
        if stale > 0:
            return {"status": "degraded", "stale_jobs": stale}
        return {"status": "operational"}
    except Exception:
        return {"status": "unknown"}


def _check_email() -> dict:
    if os.getenv("AZURE_COMMUNICATION_CONNECTION_STRING"):
        return {"status": "operational", "from": os.getenv("PRISMRAG_EMAIL_FROM", "prismrag@insightits.com")}
    return {"status": "degraded", "note": "Azure Communication Services not configured"}


def _check_stripe() -> dict:
    from prismrag.billing.catalog import stripe_status

    info = stripe_status()
    if info["configured"] and info["webhook_secret_set"]:
        return {"status": "operational", "plans": list(info["price_ids"].keys())}
    if info["secret_key_set"] and info["price_ids"]:
        return {"status": "degraded", "note": "Stripe partially configured", **info}
    return {"status": "degraded", "note": "Stripe billing not configured"}


def _overall(components: dict) -> str:
    statuses = [c.get("status") for c in components.values()]
    if "outage" in statuses:
        return "major_outage"
    if "degraded" in statuses:
        return "degraded"
    return "operational"


@router.get("")
def public_status():
    """Public status for status page and monitoring."""
    components = {
        "api":        {"status": "operational"},
        "database":   _check_db(),
        "redis":      _check_redis(),
        "job_worker": _check_worker(),
        "email":      _check_email(),
        "billing":    _check_stripe(),
        "search":     {"status": "operational" if os.getenv("GEMINI_API_KEY") else "degraded"},
    }
    incidents = _active_incidents()
    return {
        "service": "PrismRAG",
        "status": _overall(components),
        "sla_uptime_target_pct": SLA_UPTIME_TARGET,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "components": components,
        "incidents": incidents,
        "regions": _region_status(),
    }


@router.get("/sla")
def sla_summary():
    """SLA metrics summary (rolling 30-day window from health checks)."""
    return {
        "service": "PrismRAG",
        "period": "rolling_30_days",
        "uptime_target_pct": SLA_UPTIME_TARGET,
        "support_tiers": {
            "free":         {"response": "community",  "uptime_sla": None},
            "starter":      {"response": "24h email",  "uptime_sla": None},
            "professional": {"response": "8h priority", "uptime_sla": "99.5%"},
            "enterprise":   {"response": "1h dedicated", "uptime_sla": "99.9%"},
        },
        "credit_policy": "Enterprise customers receive service credits per executed SLA agreement.",
        "status_page": "/status.html",
        "contact": "prismrag@insightits.com",
    }


def _active_incidents() -> list:
    from prismrag.db import get_conn, release_conn
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id::text, title, status, impact, component, message, started_at
                FROM prismrag.status_incident
                WHERE resolved_at IS NULL
                ORDER BY started_at DESC LIMIT 10
                """
            )
            return [
                {
                    "id": r[0], "title": r[1], "status": r[2], "impact": r[3],
                    "component": r[4], "message": r[5],
                    "started_at": r[6].isoformat() if r[6] else None,
                }
                for r in cur.fetchall()
            ]
        finally:
            release_conn(conn)
    except Exception:
        return []


def _region_status() -> list:
    from prismrag.regions import list_regions
    return list_regions()
