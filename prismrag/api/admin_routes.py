"""PrismRAG — superadmin API (internal use only)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from prismrag.auth.auth import get_current_user
from prismrag.db import get_conn, release_conn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


def _require_superadmin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return user


# ── Tenants ───────────────────────────────────────────────────────────────────

@router.get("/tenants")
def list_tenants(
    search: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    _: dict = Depends(_require_superadmin),
):
    """List all tenants with usage summary."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        where = "WHERE t.name ILIKE %s" if search else ""
        params = [f"%{search}%"] if search else []
        cur.execute(f"""
            SELECT
                t.id, t.name, t.owner_email, t.tier,
                t.data_region, t.created_at,
                COUNT(DISTINCT u.id)  AS user_count,
                COALESCE(SUM(ue.quantity), 0) AS total_events
            FROM prismrag.tenant t
            LEFT JOIN prismrag.tenant_member tm ON tm.tenant_id = t.id
            LEFT JOIN prismrag.user u ON u.id = tm.user_id
            LEFT JOIN prismrag.usage_event ue ON ue.tenant_id = t.id
            {where}
            GROUP BY t.id, t.name, t.owner_email, t.tier, t.data_region, t.created_at
            ORDER BY t.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cur.fetchall()

        cur.execute(f"SELECT COUNT(*) FROM prismrag.tenant t {where}", params)
        total = cur.fetchone()[0]

        return {
            "total": total,
            "tenants": [
                {
                    "id": r[0], "name": r[1], "owner_email": r[2],
                    "tier": r[3], "data_region": r[4],
                    "created_at": r[5].isoformat() if r[5] else None,
                    "user_count": r[6], "total_events": r[7],
                }
                for r in rows
            ],
        }
    finally:
        release_conn(conn)


@router.get("/tenants/{tenant_id}")
def get_tenant(tenant_id: str, _: dict = Depends(_require_superadmin)):
    """Tenant detail + 30-day usage breakdown."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, owner_email, tier, data_region, created_at FROM prismrag.tenant WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")

        cur.execute("""
            SELECT event_type, SUM(quantity)
            FROM prismrag.usage_event
            WHERE tenant_id = %s AND created_at >= NOW() - INTERVAL '30 days'
            GROUP BY event_type
        """, (tenant_id,))
        usage = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute("""
            SELECT u.id, u.email, tm.role, u.created_at
            FROM prismrag.tenant_member tm
            JOIN prismrag.user u ON u.id = tm.user_id
            WHERE tm.tenant_id = %s
            ORDER BY u.created_at
        """, (tenant_id,))
        members = [{"id": r[0], "email": r[1], "role": r[2], "joined": r[3].isoformat() if r[3] else None} for r in cur.fetchall()]

        return {
            "id": row[0], "name": row[1], "owner_email": row[2],
            "tier": row[3], "data_region": row[4],
            "created_at": row[5].isoformat() if row[5] else None,
            "usage_30d": usage,
            "members": members,
        }
    finally:
        release_conn(conn)


@router.patch("/tenants/{tenant_id}/plan")
def update_tenant_plan(
    tenant_id: str,
    payload: dict,
    _: dict = Depends(_require_superadmin),
):
    """Change a tenant's plan tier."""
    tier = payload.get("tier")
    if not tier:
        raise HTTPException(status_code=422, detail="tier required")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE prismrag.tenant SET tier = %s WHERE id = %s RETURNING id", (tier, tenant_id))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Tenant not found")
        conn.commit()
        return {"tenant_id": tenant_id, "tier": tier}
    finally:
        release_conn(conn)


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(
    search: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    _: dict = Depends(_require_superadmin),
):
    """List all users."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        where = "WHERE email ILIKE %s" if search else ""
        params = [f"%{search}%"] if search else []
        cur.execute(f"""
            SELECT id, email, role, plan, created_at, last_login_at
            FROM prismrag.user {where}
            ORDER BY created_at DESC LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cur.fetchall()
        cur.execute(f"SELECT COUNT(*) FROM prismrag.user {where}", params)
        total = cur.fetchone()[0]
        return {
            "total": total,
            "users": [
                {
                    "id": r[0], "email": r[1], "role": r[2],
                    "plan": r[3],
                    "created_at": r[4].isoformat() if r[4] else None,
                    "last_login_at": r[5].isoformat() if r[5] else None,
                }
                for r in rows
            ],
        }
    finally:
        release_conn(conn)


@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: str,
    payload: dict,
    admin: dict = Depends(_require_superadmin),
):
    """Promote/demote a user's role."""
    role = payload.get("role")
    if role not in ("user", "admin", "superadmin"):
        raise HTTPException(status_code=422, detail="Invalid role")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE prismrag.user SET role = %s WHERE id = %s RETURNING id", (role, user_id))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="User not found")
        conn.commit()
        log.info("Superadmin %s set user %s role → %s", admin["email"], user_id, role)
        return {"user_id": user_id, "role": role}
    finally:
        release_conn(conn)


# ── Platform-wide metrics ─────────────────────────────────────────────────────

@router.get("/metrics/platform")
def platform_metrics(
    days: int = Query(7, le=90),
    _: dict = Depends(_require_superadmin),
):
    """Platform-wide usage and quality metrics."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        since = datetime.utcnow() - timedelta(days=days)

        cur.execute("SELECT COUNT(*) FROM prismrag.tenant")
        tenants_total = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM prismrag.user")
        users_total = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM prismrag.user WHERE created_at >= %s", (since,))
        new_users = cur.fetchone()[0]

        cur.execute("""
            SELECT event_type, SUM(quantity)
            FROM prismrag.usage_event
            WHERE created_at >= %s
            GROUP BY event_type
        """, (since,))
        usage = {r[0]: int(r[1]) for r in cur.fetchall()}

        # Quality P50/P95 from quality log
        try:
            cur.execute("""
                SELECT
                    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY mean_score) AS p50_score,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY mean_score) AS p95_score,
                    COUNT(*) AS search_count
                FROM prismrag.quality_search_log
                WHERE created_at >= %s
            """, (since,))
            q = cur.fetchone()
            quality = {"p50_score": float(q[0]) if q[0] else None, "p95_score": float(q[1]) if q[1] else None, "search_count": q[2]}
        except Exception:
            quality = {}

        return {
            "period_days": days,
            "tenants_total": tenants_total,
            "users_total": users_total,
            "new_users": new_users,
            "usage": usage,
            "quality": quality,
        }
    finally:
        release_conn(conn)


# ── Audit log ────────────────────────────────────────────────────────────────

@router.get("/audit")
def audit_log(
    tenant_id: str | None = None,
    user_email: str | None = None,
    action: str | None = None,
    days: int = Query(7, le=30),
    limit: int = Query(100, le=500),
    _: dict = Depends(_require_superadmin),
):
    """Search the audit log."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        conditions = ["created_at >= NOW() - INTERVAL '%s days'"]
        params: list = [days]
        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)
        if user_email:
            conditions.append("user_email ILIKE %s")
            params.append(f"%{user_email}%")
        if action:
            conditions.append("action ILIKE %s")
            params.append(f"%{action}%")

        where = "WHERE " + " AND ".join(conditions)
        cur.execute(f"""
            SELECT id, tenant_id, user_email, action, resource_type,
                   resource_id, ip_address, created_at
            FROM prismrag.audit_log
            {where}
            ORDER BY created_at DESC LIMIT %s
        """, params + [limit])

        return [
            {
                "id": r[0], "tenant_id": r[1], "user_email": r[2],
                "action": r[3], "resource_type": r[4], "resource_id": r[5],
                "ip": r[6], "created_at": r[7].isoformat() if r[7] else None,
            }
            for r in cur.fetchall()
        ]
    finally:
        release_conn(conn)
