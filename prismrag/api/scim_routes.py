"""PrismRAG — SCIM 2.0 provisioning (RFC 7644 subset)."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/scim/v2", tags=["SCIM"])


# ── SCIM auth ─────────────────────────────────────────────────────────────────

def get_scim_org(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="SCIM bearer token required")
    token = auth[7:].strip()
    if not token.startswith("sct_"):
        raise HTTPException(status_code=401, detail="Invalid SCIM token format")

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT st.organization_id::text, o.name, o.scim_enabled, o.slug, o.mfa_required
            FROM prismrag.scim_token st
            JOIN prismrag.organization o ON o.id = st.organization_id
            WHERE st.token_hash = %s AND st.is_active AND o.scim_enabled
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid SCIM token")
        cur.execute(
            "UPDATE prismrag.scim_token SET last_used_at = now() WHERE token_hash = %s",
            (token_hash,),
        )
        conn.commit()
    finally:
        release_conn(conn)

    return {
        "organization_id": row[0],
        "name": row[1],
        "slug": row[3],
        "mfa_required": row[4],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scim_user_resource(user_row: tuple) -> dict:
    uid, email, name, active, ext_id = user_row
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(uid),
        "externalId": ext_id,
        "userName": email,
        "name": {"formatted": name or email},
        "emails": [{"value": email, "primary": True}],
        "active": active,
        "meta": {"resourceType": "User"},
    }


def _find_user_by_id(org_id: str, user_id: str) -> dict | None:
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, email, full_name, is_active, scim_external_id
            FROM prismrag.user_account
            WHERE id = %s AND organization_id = %s
            """,
            (user_id, org_id),
        )
        row = cur.fetchone()
    finally:
        release_conn(conn)
    return _scim_user_resource(row) if row else None


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/Users")
def list_users(
    request: Request,
    startIndex: int = 1,
    count: int = 100,
    filter: str | None = None,
    org: dict = Depends(get_scim_org),
):
    from prismrag.db import get_conn, release_conn

    email_filter = None
    if filter and "userName eq" in filter:
        email_filter = filter.split('"')[1].lower()

    conn = get_conn()
    try:
        cur = conn.cursor()
        if email_filter:
            cur.execute(
                """
                SELECT id, email, full_name, is_active, scim_external_id
                FROM prismrag.user_account
                WHERE organization_id = %s AND email = %s
                """,
                (org["organization_id"], email_filter),
            )
        else:
            cur.execute(
                """
                SELECT id, email, full_name, is_active, scim_external_id
                FROM prismrag.user_account
                WHERE organization_id = %s
                ORDER BY created_at LIMIT %s OFFSET %s
                """,
                (org["organization_id"], count, max(0, startIndex - 1)),
            )
        rows = cur.fetchall()
        cur.execute(
            "SELECT COUNT(*) FROM prismrag.user_account WHERE organization_id = %s",
            (org["organization_id"],),
        )
        total = cur.fetchone()[0]
    finally:
        release_conn(conn)

    resources = [_scim_user_resource(r) for r in rows]
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": total,
        "startIndex": startIndex,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


@router.post("/Users", status_code=201)
def create_user(payload: dict, org: dict = Depends(get_scim_org)):
    from prismrag.auth.auth import hash_password
    from prismrag.db import get_conn, release_conn
    from prismrag.email.azure_acs import send_welcome_email

    email = (payload.get("userName") or payload.get("emails", [{}])[0].get("value", "")).lower()
    if not email:
        raise HTTPException(status_code=400, detail="userName or emails required")
    ext_id = payload.get("externalId")
    name = payload.get("name", {}).get("formatted", email.split("@")[0])
    active = payload.get("active", True)

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM prismrag.user_account WHERE email = %s", (email,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="User already exists")

        user_id = str(uuid.uuid4())
        pw = secrets.token_urlsafe(24)
        cur.execute(
            """
            INSERT INTO prismrag.user_account
                (id, email, password_hash, full_name, plan, organization_id,
                 scim_external_id, is_active, email_verified)
            VALUES (%s, %s, %s, %s, 'enterprise', %s, %s, %s, TRUE)
            """,
            (user_id, email, hash_password(pw), name, org["organization_id"], ext_id, active),
        )
        conn.commit()
    finally:
        release_conn(conn)

    send_welcome_email(email, name)
    return _find_user_by_id(org["organization_id"], user_id)


@router.get("/Users/{user_id}")
def get_user(user_id: str, org: dict = Depends(get_scim_org)):
    user = _find_user_by_id(org["organization_id"], user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/Users/{user_id}")
def patch_user(user_id: str, payload: dict, org: dict = Depends(get_scim_org)):
    from prismrag.db import get_conn, release_conn

    active = None
    for op in payload.get("Operations", []):
        if op.get("op", "").lower() == "replace" and op.get("path") == "active":
            active = bool(op.get("value"))

    conn = get_conn()
    try:
        cur = conn.cursor()
        if active is not None:
            cur.execute(
                """
                UPDATE prismrag.user_account SET is_active = %s, updated_at = now()
                WHERE id = %s AND organization_id = %s
                """,
                (active, user_id, org["organization_id"]),
            )
        conn.commit()
    finally:
        release_conn(conn)

    user = _find_user_by_id(org["organization_id"], user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.delete("/Users/{user_id}", status_code=204)
def delete_user(user_id: str, org: dict = Depends(get_scim_org)):
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE prismrag.user_account SET is_active = FALSE, updated_at = now()
            WHERE id = %s AND organization_id = %s
            """,
            (user_id, org["organization_id"]),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return Response(status_code=204)


# ── ServiceProviderConfig ─────────────────────────────────────────────────────

@router.get("/ServiceProviderConfig")
def service_provider_config():
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False},
        "filter": {"supported": True, "maxResults": 200},
        "authenticationSchemes": [{
            "type": "oauthbearertoken",
            "name": "Bearer Token",
            "description": "SCIM bearer token (sct_...)",
        }],
    }


# ── Admin: create org + SCIM token (JWT owner) ────────────────────────────────

class CreateOrgIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    data_region: str = "us-east"
    scim_enabled: bool = True
    mfa_required: bool = False


class ScimTokenOut(BaseModel):
    token: str
    prefix: str
    organization_id: str
    scim_base_url: str


class OrgOut(BaseModel):
    organization_id: str
    name: str
    slug: str
    data_region: str
    scim_enabled: bool
    mfa_required: bool
    cmek_enabled: bool


class PatchOrgIn(BaseModel):
    mfa_required: bool | None = None
    scim_enabled: bool | None = None


class CmekIn(BaseModel):
    vault_url: str
    key_name: str


def register_scim_admin_routes(auth_router):
    """Attach org/SCIM admin endpoints to auth router."""
    from prismrag.auth.auth import get_current_user, require_plan
    from prismrag.regions import validate_region

    def _load_user_org(user_id: str) -> tuple | None:
        from prismrag.db import get_conn, release_conn

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT o.id::text, o.name, o.slug, o.data_region, o.scim_enabled,
                       o.mfa_required, o.cmek_enabled
                FROM prismrag.user_account u
                JOIN prismrag.organization o ON o.id = u.organization_id
                WHERE u.id = %s
                """,
                (user_id,),
            )
            return cur.fetchone()
        finally:
            release_conn(conn)

    @auth_router.get("/organizations/me", response_model=OrgOut)
    def get_my_organization(user: dict = Depends(get_current_user)):
        row = _load_user_org(user["id"])
        if not row:
            raise HTTPException(status_code=404, detail="No organization linked to this account")
        return OrgOut(
            organization_id=row[0],
            name=row[1],
            slug=row[2],
            data_region=row[3],
            scim_enabled=row[4],
            mfa_required=row[5],
            cmek_enabled=row[6],
        )

    @auth_router.patch("/organizations/me", response_model=OrgOut)
    def patch_my_organization(
        body: PatchOrgIn,
        user: dict = Depends(require_plan("enterprise")),
    ):
        from prismrag.db import get_conn, release_conn

        row = _load_user_org(user["id"])
        if not row:
            raise HTTPException(status_code=404, detail="No organization linked to this account")
        org_id = row[0]

        updates = []
        params: list = []
        if body.mfa_required is not None:
            updates.append("mfa_required = %s")
            params.append(body.mfa_required)
        if body.scim_enabled is not None:
            updates.append("scim_enabled = %s")
            params.append(body.scim_enabled)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(org_id)
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE prismrag.organization SET {', '.join(updates)}, updated_at = now() WHERE id = %s",
                params,
            )
            conn.commit()
        finally:
            release_conn(conn)
        return get_my_organization(user)

    @auth_router.post("/organizations", status_code=201)
    def create_organization(
        body: CreateOrgIn,
        user: dict = Depends(require_plan("enterprise")),
    ):
        from prismrag.db import get_conn, release_conn

        validate_region(body.data_region)
        org_id = str(uuid.uuid4())
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO prismrag.organization
                    (id, name, slug, data_region, scim_enabled, mfa_required)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (org_id, body.name, body.slug, body.data_region, body.scim_enabled, body.mfa_required),
            )
            cur.execute(
                "UPDATE prismrag.user_account SET organization_id = %s WHERE id = %s",
                (org_id, user["id"]),
            )
            conn.commit()
        finally:
            release_conn(conn)
        return {"organization_id": org_id, "name": body.name, "slug": body.slug}

    @auth_router.post("/organizations/scim-token", response_model=ScimTokenOut)
    def create_scim_token(
        label: str = "Okta",
        user: dict = Depends(get_current_user),
    ):
        from prismrag.db import get_conn, release_conn
        import os

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT organization_id::text FROM prismrag.user_account WHERE id = %s",
                (user["id"],),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                raise HTTPException(status_code=400, detail="Create an organization first")
            org_id = row[0]

            raw = "sct_" + secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw.encode()).hexdigest()
            prefix = raw[:16]
            cur.execute(
                """
                INSERT INTO prismrag.scim_token (organization_id, token_hash, token_prefix, label)
                VALUES (%s, %s, %s, %s)
                """,
                (org_id, token_hash, prefix, label),
            )
            cur.execute(
                "UPDATE prismrag.organization SET scim_enabled = TRUE WHERE id = %s",
                (org_id,),
            )
            conn.commit()
        finally:
            release_conn(conn)

        base = os.getenv("PRISMRAG_BASE_URL", "http://localhost:8001")
        return ScimTokenOut(
            token=raw,
            prefix=prefix,
            organization_id=org_id,
            scim_base_url=f"{base}/api/v1/scim/v2",
        )

    @auth_router.post("/organizations/cmek")
    def configure_org_cmek(
        body: CmekIn,
        user: dict = Depends(require_plan("enterprise")),
    ):
        from prismrag.cmek import configure_cmek
        from prismrag.db import get_conn, release_conn

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT organization_id::text FROM prismrag.user_account WHERE id = %s",
                (user["id"],),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                raise HTTPException(status_code=400, detail="Create an organization first")
            org_id = row[0]
        finally:
            release_conn(conn)

        try:
            return configure_cmek(org_id, body.vault_url, body.key_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @auth_router.get("/regions")
    def list_regions():
        from prismrag.regions import list_regions as _list
        return {"regions": _list()}
