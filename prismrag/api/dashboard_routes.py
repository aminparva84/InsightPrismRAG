"""PrismRAG — per-tenant self-serve usage dashboard API."""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query

from prismrag.auth.auth import get_current_user
from prismrag.db import get_conn, release_conn

router = APIRouter(prefix="/api/v1/dashboard", tags=["Dashboard"])

PLAN_LIMITS = {
    "starter":      {"searches": 5_000,   "deliberations": 50},
    "professional": {"searches": 50_000,  "deliberations": 500},
    "enterprise":   {"searches": 500_000, "deliberations": 5_000},
}


def _billing_period() -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    _, last_day = calendar.monthrange(now.year, now.month)
    end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    return start, end


@router.get("/usage")
def tenant_usage(user: dict = Depends(get_current_user)):
    """Current billing period usage for the caller's tenant."""
    tenant_id = user.get("tenant_id")
    conn = get_conn()
    try:
        cur = conn.cursor()
        period_start, period_end = _billing_period()

        # Current period events
        cur.execute("""
            SELECT event_type, SUM(quantity)
            FROM prismrag.usage_event
            WHERE tenant_id = %s AND created_at BETWEEN %s AND %s
            GROUP BY event_type
        """, (tenant_id, period_start, period_end))
        usage = {r[0]: int(r[1]) for r in cur.fetchall()}

        # Tenant plan
        cur.execute("SELECT tier FROM prismrag.tenant WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
        tier = (row[0] if row else "starter") or "starter"
        limits = PLAN_LIMITS.get(tier, PLAN_LIMITS["starter"])

        searches = usage.get("search", 0)
        deliberations = usage.get("deliberation", 0)

        # Daily breakdown for chart (last 30 days)
        cur.execute("""
            SELECT
                DATE_TRUNC('day', created_at) AS day,
                event_type,
                SUM(quantity) AS qty
            FROM prismrag.usage_event
            WHERE tenant_id = %s AND created_at >= NOW() - INTERVAL '30 days'
            GROUP BY day, event_type
            ORDER BY day
        """, (tenant_id,))
        daily = {}
        for day, event_type, qty in cur.fetchall():
            key = day.strftime("%Y-%m-%d")
            if key not in daily:
                daily[key] = {}
            daily[key][event_type] = int(qty)

        # Deliberation overages for current period
        deliberation_limit = limits["deliberations"]
        overage_count = max(0, deliberations - deliberation_limit)
        overage_cost = round(overage_count * 0.25, 2)

        return {
            "tenant_id": tenant_id,
            "tier": tier,
            "billing_period": {
                "start": period_start.date().isoformat(),
                "end": period_end.date().isoformat(),
            },
            "usage": {
                "searches": searches,
                "deliberations": deliberations,
            },
            "limits": limits,
            "remaining": {
                "searches": max(0, limits["searches"] - searches),
                "deliberations": max(0, limits["deliberations"] - deliberations),
            },
            "overage": {
                "deliberations": overage_count,
                "estimated_cost_usd": overage_cost,
            },
            "daily_breakdown": daily,
        }
    finally:
        release_conn(conn)


@router.get("/quality")
def tenant_quality(
    days: int = Query(7, le=30),
    user: dict = Depends(get_current_user),
):
    """Quality metrics for the caller's tenant (latency, relevance scores)."""
    tenant_id = user.get("tenant_id")
    conn = get_conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT
                    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY mean_score) AS p50_score,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY mean_score) AS p95_score,
                    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency,
                    COUNT(*) AS count
                FROM prismrag.quality_search_log
                WHERE tenant_id = %s AND created_at >= NOW() - INTERVAL '%s days'
            """, (tenant_id, days))
            q = cur.fetchone()
            search_quality = {
                "p50_score": round(float(q[0]), 4) if q[0] else None,
                "p95_score": round(float(q[1]), 4) if q[1] else None,
                "p50_latency_ms": round(float(q[2]), 1) if q[2] else None,
                "p95_latency_ms": round(float(q[3]), 1) if q[3] else None,
                "count": q[4],
            }
        except Exception:
            search_quality = {}

        return {"period_days": days, "search": search_quality}
    finally:
        release_conn(conn)


@router.get("/invoices")
def tenant_invoices(user: dict = Depends(get_current_user)):
    """Recent Stripe invoices (last 12 months)."""
    tenant_id = user.get("tenant_id")
    conn = get_conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT stripe_invoice_id, amount_due, amount_paid,
                       currency, status, period_start, period_end, hosted_invoice_url
                FROM prismrag.invoice
                WHERE tenant_id = %s
                ORDER BY period_start DESC LIMIT 12
            """, (tenant_id,))
            rows = cur.fetchall()
        except Exception:
            rows = []

        return [
            {
                "id": r[0], "amount_due": r[1], "amount_paid": r[2],
                "currency": r[3], "status": r[4],
                "period_start": r[5].isoformat() if r[5] else None,
                "period_end": r[6].isoformat() if r[6] else None,
                "url": r[7],
            }
            for r in rows
        ]
    finally:
        release_conn(conn)
