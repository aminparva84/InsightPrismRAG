"""PrismRAG — Auth API routes."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from prismrag.auth.auth import (
    create_jwt, generate_api_key, get_current_user,
    hash_password, verify_password,
)
from prismrag.db import get_conn, release_conn

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
auth_router = router  # alias used in main.py


# ── Pydantic models ───────────────────────────────────────────────────────────

class RegisterIn(BaseModel):
    email:     str = Field(..., min_length=3)
    password:  str = Field(..., min_length=8)
    full_name: str = Field("", max_length=200)
    company:   str = Field("", max_length=200)


class LoginIn(BaseModel):
    email:    str
    password: str


class TokenOut(BaseModel):
    token:     str
    user_id:   str
    email:     str
    plan:      str
    full_name: str


class LoginResponse(BaseModel):
    mfa_required: bool = False
    mfa_token:    str | None = None
    token:        str | None = None
    user_id:      str | None = None
    email:        str | None = None
    plan:         str | None = None
    full_name:    str | None = None


class MfaVerifyIn(BaseModel):
    mfa_token: str
    code:      str = Field(..., min_length=6, max_length=8)


class MfaEnrollStartOut(BaseModel):
    secret:    str
    otpauth_uri: str


class MfaEnrollConfirmIn(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class MfaDisableIn(BaseModel):
    password: str
    code:     str


class APIKeyOut(BaseModel):
    raw_key:    str   # shown ONCE — user must copy it
    key_prefix: str
    label:      str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenOut)
def register(body: RegisterIn):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM prismrag.user_account WHERE email = %s", (body.email.lower(),))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="Email already registered")

        user_id = str(uuid.uuid4())
        pw_hash = hash_password(body.password)
        cur.execute(
            """
            INSERT INTO prismrag.user_account
                (id, email, password_hash, full_name, company, plan)
            VALUES (%s, %s, %s, %s, %s, 'free')
            """,
            (user_id, body.email.lower(), pw_hash, body.full_name, body.company),
        )

        # Create a default tenant for this user
        tenant_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO prismrag.tenant (id, name, owner_email) VALUES (%s, %s, %s)",
            (tenant_id, body.company or body.full_name or "My Workspace", body.email.lower()),
        )
        cur.execute(
            """
            INSERT INTO prismrag.tenant_member (tenant_id, user_id, role)
            VALUES (%s, %s, 'owner')
            """,
            (tenant_id, user_id),
        )
        conn.commit()
    finally:
        release_conn(conn)

    from prismrag.tasks.dispatch import run_in_thread
    from prismrag.email.azure_acs import send_welcome_email
    run_in_thread(send_welcome_email, body.email.lower(), body.full_name)

    token = create_jwt(user_id, body.email.lower(), "free")
    return TokenOut(
        token=token, user_id=user_id,
        email=body.email.lower(), plan="free", full_name=body.full_name,
    )


@router.post("/login", response_model=LoginResponse)
def login(body: LoginIn):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, email, password_hash, full_name, plan, is_active, mfa_enabled "
            "FROM prismrag.user_account WHERE email = %s",
            (body.email.lower(),),
        )
        row = cur.fetchone()
    finally:
        release_conn(conn)

    if not row or not row[5]:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not row[2]:
        raise HTTPException(
            status_code=401,
            detail="This account uses SSO. Sign in via /api/v1/auth/oidc/login",
        )
    if not verify_password(body.password, row[2]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_id, email, _, full_name, plan, _, mfa_enabled = row

    if mfa_enabled:
        from prismrag.auth.mfa import create_mfa_challenge
        return LoginResponse(
            mfa_required=True,
            mfa_token=create_mfa_challenge(str(user_id)),
            email=email,
            user_id=str(user_id),
        )

    token = create_jwt(str(user_id), email, plan)
    return LoginResponse(
        mfa_required=False,
        token=token,
        user_id=str(user_id),
        email=email,
        plan=plan,
        full_name=full_name or "",
    )


@router.post("/login/mfa", response_model=TokenOut)
def login_mfa(body: MfaVerifyIn):
    from prismrag.auth.mfa import (
        consume_mfa_challenge, verify_totp, verify_backup_code,
    )

    user_id = consume_mfa_challenge(body.mfa_token)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT email, full_name, plan, mfa_secret, mfa_enabled, is_active "
            "FROM prismrag.user_account WHERE id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    finally:
        release_conn(conn)

    if not row or not row[5] or not row[4]:
        raise HTTPException(status_code=401, detail="MFA not enabled")

    email, full_name, plan, secret, _, _ = row
    ok = verify_totp(secret, body.code) or verify_backup_code(user_id, body.code)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid MFA code")

    token = create_jwt(user_id, email, plan)
    return TokenOut(
        token=token, user_id=user_id,
        email=email, plan=plan, full_name=full_name or "",
    )


@router.post("/mfa/enroll/start", response_model=MfaEnrollStartOut)
def mfa_enroll_start(user: dict = Depends(get_current_user)):
    from prismrag.auth.mfa import generate_totp_secret, totp_uri, get_mfa_status

    status = get_mfa_status(user["id"])
    if status["mfa_enabled"]:
        raise HTTPException(status_code=400, detail="MFA already enabled")

    secret = generate_totp_secret()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE prismrag.user_account SET mfa_secret = %s WHERE id = %s",
            (secret, user["id"]),
        )
        conn.commit()
    finally:
        release_conn(conn)

    return MfaEnrollStartOut(secret=secret, otpauth_uri=totp_uri(secret, user["email"]))


@router.post("/mfa/enroll/confirm")
def mfa_enroll_confirm(body: MfaEnrollConfirmIn, user: dict = Depends(get_current_user)):
    from prismrag.auth.mfa import verify_totp, generate_backup_codes, hash_backup_code
    from prismrag.tasks.dispatch import run_in_thread
    from prismrag.email.azure_acs import send_mfa_enabled_email

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT mfa_secret FROM prismrag.user_account WHERE id = %s",
            (user["id"],),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            raise HTTPException(status_code=400, detail="Call /mfa/enroll/start first")
        if not verify_totp(row[0], body.code):
            raise HTTPException(status_code=400, detail="Invalid code — check your authenticator app")

        codes = generate_backup_codes()
        hashed = [hash_backup_code(c) for c in codes]
        cur.execute(
            """
            UPDATE prismrag.user_account
            SET mfa_enabled = TRUE, mfa_backup_codes = %s, updated_at = now()
            WHERE id = %s
            """,
            (hashed, user["id"]),
        )
        conn.commit()
    finally:
        release_conn(conn)

    run_in_thread(send_mfa_enabled_email, user["email"])
    return {"mfa_enabled": True, "backup_codes": codes}


@router.post("/mfa/disable")
def mfa_disable(body: MfaDisableIn, user: dict = Depends(get_current_user)):
    from prismrag.auth.mfa import verify_totp

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT password_hash, mfa_secret FROM prismrag.user_account WHERE id = %s",
            (user["id"],),
        )
        row = cur.fetchone()
        if not row or not verify_password(body.password, row[0]):
            raise HTTPException(status_code=401, detail="Invalid password")
        if not verify_totp(row[1], body.code):
            raise HTTPException(status_code=400, detail="Invalid MFA code")

        cur.execute(
            """
            UPDATE prismrag.user_account
            SET mfa_enabled = FALSE, mfa_secret = NULL, mfa_backup_codes = '{}'
            WHERE id = %s
            """,
            (user["id"],),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return {"mfa_enabled": False}


@router.get("/mfa/status")
def mfa_status(user: dict = Depends(get_current_user)):
    from prismrag.auth.mfa import get_mfa_status
    return get_mfa_status(user["id"])


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user


@router.post("/api-keys", response_model=APIKeyOut)
def create_api_key(
    label: str = "Default",
    scopes: str = "read,write",
    user: dict = Depends(get_current_user),
):
    raw, key_hash, prefix = generate_api_key()
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()] or ["read", "write"]
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO prismrag.api_key (user_id, key_hash, key_prefix, label, scopes) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user["id"], key_hash, prefix, label, scope_list),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return APIKeyOut(raw_key=raw, key_prefix=prefix, label=label)


@router.get("/api-keys")
def list_api_keys(user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, key_prefix, label, is_active, last_used_at, created_at "
            "FROM prismrag.api_key WHERE user_id = %s ORDER BY created_at DESC",
            (user["id"],),
        )
        return [
            {
                "id": str(r[0]), "keyPrefix": r[1], "label": r[2],
                "isActive": r[3],
                "lastUsedAt": r[4].isoformat() if r[4] else None,
                "createdAt": r[5].isoformat() if r[5] else None,
            }
            for r in cur.fetchall()
        ]
    finally:
        release_conn(conn)


@router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: str, user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE prismrag.api_key SET is_active = FALSE "
            "WHERE id = %s AND user_id = %s",
            (key_id, user["id"]),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return {"revoked": key_id}


@router.get("/usage")
def usage_this_month(user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT event_type, SUM(units) AS total
            FROM prismrag.usage_event
            WHERE user_id = %s
              AND created_at >= date_trunc('month', now())
            GROUP BY event_type
            """,
            (user["id"],),
        )
        usage = {r[0]: int(r[1]) for r in cur.fetchall()}

        cur.execute(
            "SELECT monthly_chunks, max_tenants, tier2_mlp, graph_rag, bridge_vectors "
            "FROM prismrag.plan_quota WHERE plan = %s",
            (user["plan"],),
        )
        row = cur.fetchone()
        quota = {
            "monthlyChunks": row[0] if row else 5000,
            "maxTenants":    row[1] if row else 1,
            "tier2Mlp":      row[2] if row else False,
            "graphRag":      row[3] if row else False,
            "bridgeVectors": row[4] if row else False,
        } if row else {}

        cur.execute(
            "SELECT COUNT(*) FROM prismrag.tenant WHERE owner_email = %s",
            (user.get("email", ""),),
        )
        tenants_count = cur.fetchone()[0]
    finally:
        release_conn(conn)

    from prismrag.plans import get_plan_limits
    limits = get_plan_limits(user["plan"])

    chunks_used   = usage.get("ingest_chunk", 0)
    searches_used = usage.get("search", 0)
    chunks_limit  = limits["monthly_chunks"]   # 0 = unlimited
    search_limit  = limits["monthly_searches"]  # 0 = unlimited

    return {
        # Dashboard-compatible keys
        "plan":           user["plan"],
        "chunks_used":    chunks_used,
        "chunks_limit":   chunks_limit,
        "searches_used":  searches_used,
        "searches_limit": search_limit,
        "tenants_count":  tenants_count,
        # Detailed breakdown
        "usage": usage,
        "quota": quota,
    }


@router.get("/oidc/login")
def oidc_login():
    """Redirect to OIDC provider (configure OIDC_* env vars)."""
    from fastapi.responses import RedirectResponse
    from prismrag.auth.oidc import build_authorize_url, oidc_enabled

    if not oidc_enabled():
        raise HTTPException(
            status_code=501,
            detail="OIDC not configured. Set OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_REDIRECT_URI.",
        )
    url, _state = build_authorize_url()
    return RedirectResponse(url)


@router.get("/oidc/callback", response_model=TokenOut)
def oidc_callback(code: str, state: str):
    """OIDC callback — exchanges code and returns JWT."""
    from prismrag.auth.oidc import (
        consume_state, exchange_code, find_or_create_user, oidc_enabled,
    )

    if not oidc_enabled():
        raise HTTPException(status_code=501, detail="OIDC not configured")
    if not consume_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    try:
        claims = exchange_code(code)
        user = find_or_create_user(claims)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    token = create_jwt(user["id"], user["email"], user["plan"])
    return TokenOut(
        token=token,
        user_id=user["id"],
        email=user["email"],
        plan=user["plan"],
        full_name=user.get("fullName") or "",
    )


@router.get("/oidc/status")
def oidc_status():
    from prismrag.auth.oidc import oidc_enabled
    return {"enabled": oidc_enabled()}


# SCIM org admin routes (enterprise)
from prismrag.api.scim_routes import register_scim_admin_routes
register_scim_admin_routes(router)
