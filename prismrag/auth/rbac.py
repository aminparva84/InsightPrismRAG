"""PrismRAG — Role-based access control for workspaces."""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException

Permission = Literal["read", "write", "admin", "delete"]

ROLE_RANK = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "viewer": {"read"},
    "member": {"read", "write"},
    "admin":  {"read", "write", "admin"},
    "owner":  {"read", "write", "admin", "delete"},
}


def role_allows(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def get_tenant_role(user_id: str, tenant_id: str) -> str | None:
    """Return role for user on tenant, or None if not a member."""
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT role FROM prismrag.tenant_member
            WHERE tenant_id = %s AND user_id = %s
            """,
            (tenant_id, user_id),
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        release_conn(conn)


def ensure_tenant_member(
    tenant_id: str,
    user_id: str,
    email: str,
    role: str = "owner",
    invited_by: str | None = None,
) -> None:
    """Add or upgrade membership (used on tenant create / invite)."""
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM prismrag.tenant WHERE id = %s",
            (tenant_id,),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} not found")

        cur.execute(
            """
            SELECT role FROM prismrag.tenant_member
            WHERE tenant_id = %s AND user_id = %s
            """,
            (tenant_id, user_id),
        )
        existing = cur.fetchone()
        final_role = role
        if existing:
            cur_role = existing[0]
            if ROLE_RANK.get(cur_role, 0) >= ROLE_RANK.get(role, 0):
                final_role = cur_role
            elif cur_role == "owner":
                final_role = "owner"

        cur.execute(
            """
            INSERT INTO prismrag.tenant_member (tenant_id, user_id, role, invited_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tenant_id, user_id) DO UPDATE SET
                role = EXCLUDED.role,
                invited_by = COALESCE(EXCLUDED.invited_by, prismrag.tenant_member.invited_by)
            """,
            (tenant_id, user_id, final_role, invited_by),
        )
        # Keep legacy owner_email in sync for owner
        if final_role == "owner":
            cur.execute(
                "UPDATE prismrag.tenant SET owner_email = %s, updated_at = now() WHERE id = %s",
                (email.lower(), tenant_id),
            )
        conn.commit()
    finally:
        release_conn(conn)


def assert_permission(
    user: dict,
    tenant_id: str,
    permission: Permission = "read",
) -> str:
    """
    Verify user has permission on tenant. Returns their role.
    Falls back to owner_email match for legacy tenants without membership rows.
    """
    user_id = user["id"]
    role = get_tenant_role(user_id, tenant_id)

    if role is None:
        # Legacy fallback: owner_email
        from prismrag.db import get_conn, release_conn

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT owner_email FROM prismrag.tenant WHERE id = %s",
                (tenant_id,),
            )
            row = cur.fetchone()
        finally:
            release_conn(conn)

        if not row:
            raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} not found")

        if (row[0] or "").lower() == (user.get("email") or "").lower():
            ensure_tenant_member(tenant_id, user_id, user.get("email", ""), "owner")
            return "owner"

        raise HTTPException(
            status_code=403,
            detail="You do not have access to this workspace.",
        )

    if not role_allows(role, permission):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' cannot perform '{permission}' on this workspace.",
        )
    return role
