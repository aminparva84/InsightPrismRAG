"""PrismRAG — IP Allowlist management (enterprise feature).

Org admins can define a CIDR allowlist. When enabled, any API request from an
IP not in the allowlist is rejected with 403 before auth even runs.

Endpoints:
  GET    /api/v1/org/{org_id}/security/ip-allowlist
  POST   /api/v1/org/{org_id}/security/ip-allowlist
  DELETE /api/v1/org/{org_id}/security/ip-allowlist/{entry_id}
  PUT    /api/v1/org/{org_id}/security/ip-allowlist/enabled
"""
from __future__ import annotations

import ipaddress
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from prismrag.auth.auth import get_current_user
from prismrag.db import get_conn, release_conn

router = APIRouter(prefix="/api/v1/org", tags=["Security"])
security_router = router


class CIDREntry(BaseModel):
    cidr: str
    label: str = ""


class AllowlistToggle(BaseModel):
    enabled: bool


def _assert_org_admin(user: dict, org_id: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tm.role FROM prismrag.tenant_member tm
            JOIN prismrag.tenant t ON t.id = tm.tenant_id
            WHERE tm.user_id = %s AND t.organization_id = %s
            """,
            (user["id"], org_id),
        )
        row = cur.fetchone()
    finally:
        release_conn(conn)
    if not row or row[0] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Organization admin required")


@router.get("/{org_id}/security/ip-allowlist")
def get_allowlist(org_id: str, user: dict = Depends(get_current_user)):
    _assert_org_admin(user, org_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, cidr, label, created_at FROM prismrag.ip_allowlist WHERE organization_id = %s ORDER BY created_at",
            (org_id,),
        )
        entries = [{"id": str(r[0]), "cidr": r[1], "label": r[2], "createdAt": r[3].isoformat()} for r in cur.fetchall()]
        cur.execute("SELECT ip_allowlist_enabled FROM prismrag.organization WHERE id = %s", (org_id,))
        row = cur.fetchone()
        enabled = bool(row[0]) if row else False
    finally:
        release_conn(conn)
    return {"enabled": enabled, "entries": entries}


@router.post("/{org_id}/security/ip-allowlist", status_code=201)
def add_allowlist_entry(org_id: str, body: CIDREntry, user: dict = Depends(get_current_user)):
    _assert_org_admin(user, org_id)
    try:
        ipaddress.ip_network(body.cidr, strict=False)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid CIDR: {body.cidr}")

    entry_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO prismrag.ip_allowlist (id, organization_id, cidr, label)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (organization_id, cidr) DO UPDATE SET label = EXCLUDED.label
            """,
            (entry_id, org_id, body.cidr, body.label),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return {"id": entry_id, "cidr": body.cidr, "label": body.label}


@router.delete("/{org_id}/security/ip-allowlist/{entry_id}", status_code=204)
def delete_allowlist_entry(org_id: str, entry_id: str, user: dict = Depends(get_current_user)):
    _assert_org_admin(user, org_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM prismrag.ip_allowlist WHERE id = %s AND organization_id = %s",
            (entry_id, org_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Entry not found")
        conn.commit()
    finally:
        release_conn(conn)


@router.put("/{org_id}/security/ip-allowlist/enabled")
def set_allowlist_enabled(org_id: str, body: AllowlistToggle, user: dict = Depends(get_current_user)):
    _assert_org_admin(user, org_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE prismrag.organization SET ip_allowlist_enabled = %s WHERE id = %s",
            (body.enabled, org_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Organization not found")
        conn.commit()
    finally:
        release_conn(conn)
    return {"enabled": body.enabled}
