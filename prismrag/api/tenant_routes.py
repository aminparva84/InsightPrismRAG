"""PrismRAG — Workspace member management and data governance."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from prismrag.auth.auth import get_current_user
from prismrag.auth.rbac import assert_permission, ensure_tenant_member, get_tenant_role, ROLE_RANK
from prismrag.auth.tenant import assert_tenant_access
from prismrag.db import get_conn, release_conn

router = APIRouter(prefix="/api/v1/tenants", tags=["Tenants"])
tenant_router = router


class InviteMemberIn(BaseModel):
    email: EmailStr
    role: Literal["admin", "member", "viewer"] = "member"


@router.get("/{tenant_id}/members")
def list_members(tenant_id: str, user: dict = Depends(get_current_user)):
    assert_tenant_access(user, tenant_id, "read")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tm.user_id, u.email, u.full_name, tm.role, tm.created_at
            FROM prismrag.tenant_member tm
            JOIN prismrag.user_account u ON u.id = tm.user_id
            WHERE tm.tenant_id = %s
            ORDER BY tm.role DESC, tm.created_at
            """,
            (tenant_id,),
        )
        return [
            {
                "user_id": str(r[0]),
                "email": r[1],
                "full_name": r[2],
                "role": r[3],
                "joined_at": r[4].isoformat() if r[4] else None,
            }
            for r in cur.fetchall()
        ]
    finally:
        release_conn(conn)


@router.post("/{tenant_id}/members", status_code=201)
def invite_member(
    tenant_id: str,
    body: InviteMemberIn,
    user: dict = Depends(get_current_user),
):
    assert_permission(user, tenant_id, "admin")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, full_name FROM prismrag.user_account WHERE email = %s",
            (body.email.lower(),),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail="User must register before they can be invited.",
            )
        target_id = str(row[0])
        ensure_tenant_member(
            tenant_id, target_id, body.email.lower(), body.role, invited_by=user["id"]
        )
        from prismrag.tasks.dispatch import run_in_thread
        from prismrag.email.azure_acs import send_member_invite_email
        run_in_thread(
            send_member_invite_email,
            body.email.lower(),
            "PrismRAG workspace",
            user.get("fullName") or user.get("email", ""),
        )
        return {"user_id": target_id, "email": body.email.lower(), "role": body.role}
    finally:
        release_conn(conn)


@router.delete("/{tenant_id}/members/{member_user_id}")
def remove_member(
    tenant_id: str,
    member_user_id: str,
    user: dict = Depends(get_current_user),
):
    actor_role = assert_permission(user, tenant_id, "admin")
    target_role = get_tenant_role(member_user_id, tenant_id)
    if not target_role:
        raise HTTPException(status_code=404, detail="Member not found")
    if target_role == "owner":
        raise HTTPException(status_code=400, detail="Cannot remove workspace owner")
    if ROLE_RANK.get(actor_role, 0) < ROLE_RANK.get(target_role, 0):
        raise HTTPException(status_code=403, detail="Cannot remove a member with equal or higher role")

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM prismrag.tenant_member WHERE tenant_id = %s AND user_id = %s",
            (tenant_id, member_user_id),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return {"removed": member_user_id}


@router.get("/{tenant_id}/export")
def export_tenant_data(tenant_id: str, user: dict = Depends(get_current_user)):
    """Export workspace mapping rules and chunk metadata (not raw vectors)."""
    assert_permission(user, tenant_id, "admin")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, tier, owner_email, created_at FROM prismrag.tenant WHERE id = %s",
            (tenant_id,),
        )
        tenant_row = cur.fetchone()
        if not tenant_row:
            raise HTTPException(status_code=404, detail="Tenant not found")

        cur.execute(
            """
            SELECT id, version, is_active, created_at
            FROM prismrag.mapping_version WHERE tenant_id = %s ORDER BY created_at
            """,
            (tenant_id,),
        )
        mappings = []
        for m in cur.fetchall():
            mid = str(m[0])
            cur.execute(
                "SELECT slug, label, description FROM prismrag.mapping_category WHERE mapping_id = %s",
                (mid,),
            )
            categories = [{"slug": r[0], "label": r[1], "description": r[2]} for r in cur.fetchall()]
            cur.execute(
                "SELECT word, category_slug, weight FROM prismrag.mapping_rule WHERE mapping_id = %s",
                (mid,),
            )
            rules = [{"word": r[0], "category_slug": r[1], "weight": float(r[2])} for r in cur.fetchall()]
            mappings.append({
                "mapping_id": mid,
                "version": m[1],
                "is_active": m[2],
                "created_at": m[3].isoformat() if m[3] else None,
                "categories": categories,
                "rules": rules,
            })

        cur.execute(
            """
            SELECT chunk_ref, category_slug, left(chunk_text, 500), created_at
            FROM prismrag.chunk_embedding
            WHERE tenant_id = %s
            ORDER BY created_at DESC LIMIT 10000
            """,
            (tenant_id,),
        )
        chunks = [
            {"ref": r[0], "category": r[1], "text_preview": r[2],
             "created_at": r[3].isoformat() if r[3] else None}
            for r in cur.fetchall()
        ]

        return {
            "tenant": {
                "id": tenant_id,
                "name": tenant_row[1],
                "tier": tenant_row[2],
                "created_at": tenant_row[4].isoformat() if tenant_row[4] else None,
            },
            "mappings": mappings,
            "chunks_sample": chunks,
            "exported_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        }
    finally:
        release_conn(conn)


@router.delete("/{tenant_id}")
def delete_tenant(tenant_id: str, user: dict = Depends(get_current_user)):
    """Permanently delete workspace and all data (owner only)."""
    assert_permission(user, tenant_id, "delete")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM prismrag.tenant WHERE id = %s", (tenant_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Tenant not found")
        conn.commit()
    finally:
        release_conn(conn)
    return {"deleted": tenant_id}
