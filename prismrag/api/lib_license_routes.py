"""
PrismRAG — Library license API.

Public:
  POST /api/v1/lib/validate   — called by prismrag-patch on startup (no auth)

Admin-only (requires superadmin JWT):
  POST /api/v1/lib/licenses           — issue a new license
  GET  /api/v1/lib/licenses           — list all licenses
  GET  /api/v1/lib/licenses/{id}      — get one license
  PUT  /api/v1/lib/licenses/{id}/status — suspend / reinstate
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from prismrag.auth.auth import get_current_user
from prismrag.db import get_conn, release_conn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/lib", tags=["Library License"])

SUPERADMIN_EMAIL = os.getenv("PRISMRAG_SUPERADMIN_EMAIL", "prismrag@insightits.com")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _require_superadmin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("email", "").lower() != SUPERADMIN_EMAIL.lower():
        raise HTTPException(status_code=403, detail="Superadmin only")
    return user


def _reset_calls_if_needed(conn, license_id: str) -> None:
    """Reset daily call counter if the reset date is in the past."""
    cur = conn.cursor()
    cur.execute("""
        UPDATE prismrag.lib_license
        SET calls_today = 0, calls_reset_date = CURRENT_DATE
        WHERE id = %s AND calls_reset_date < CURRENT_DATE
    """, (license_id,))
    conn.commit()


# ── Pydantic models ───────────────────────────────────────────────────────────

class ValidateRequest(BaseModel):
    license_key: str = Field(..., min_length=10)
    adapter:     str = Field(default="unknown", max_length=50)
    version:     str = Field(default="1.0.0",   max_length=20)


class IssueLicenseRequest(BaseModel):
    company_name:  str = Field(..., min_length=1, max_length=200)
    contact_email: str = Field(..., min_length=5, max_length=200)
    plan:          str = Field(default="annual")   # annual | monthly | enterprise
    duration_days: int = Field(default=365, ge=1, le=3650)
    stripe_subscription_id: str | None = None
    stripe_customer_id:     str | None = None


class UpdateStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(active|suspended|cancelled)$")


# ── Public: validate ──────────────────────────────────────────────────────────

@router.post("/validate")
def validate_license(body: ValidateRequest):
    """
    Called by the prismrag-patch Python library on startup.
    No authentication required — the license key IS the credential.
    Rate-limited to 500k calls/day per license.
    """
    if not body.license_key.startswith("prlib_"):
        raise HTTPException(status_code=400, detail="Invalid license key format")

    key_hash = _hash_key(body.license_key)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, company_name, contact_email, plan, status,
                   expires_at, max_calls_per_day, calls_today, calls_reset_date
            FROM prismrag.lib_license
            WHERE license_key_hash = %s
        """, (key_hash,))
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="License key not found")

        lid, company, email, plan, status, expires_at, max_calls, calls_today, reset_date = row

        if status == "suspended":
            raise HTTPException(status_code=403, detail="License suspended — contact prismrag@insightits.com")
        if status == "cancelled":
            raise HTTPException(status_code=403, detail="License cancelled")

        # Reset daily counter if needed
        _reset_calls_if_needed(conn, str(lid))

        # Increment call counter
        cur.execute("""
            UPDATE prismrag.lib_license
            SET calls_today = calls_today + 1,
                last_validated_at = now()
            WHERE id = %s
        """, (str(lid),))
        conn.commit()

        now = datetime.now(timezone.utc)
        expired = expires_at < now

        return {
            "valid":      not expired,
            "status":     "expired" if expired else "active",
            "company":    company,
            "plan":       plan,
            "expires_at": expires_at.isoformat(),
            "features":   ["pgvector", "chroma", "pinecone", "weaviate"],
            "reason":     "License expired — renew at https://prismrag.insightits.com/lib" if expired else None,
        }
    finally:
        release_conn(conn)


# ── Admin: issue license ──────────────────────────────────────────────────────

@router.post("/licenses", status_code=201)
def issue_license(
    body: IssueLicenseRequest,
    _admin: dict = Depends(_require_superadmin),
):
    """Issue a new library license. Returns the raw key (shown only once)."""
    raw_key = "prlib_" + secrets.token_hex(24)   # 54 chars total
    key_hash = _hash_key(raw_key)
    prefix = raw_key[:16]

    expires_at = datetime.now(timezone.utc) + timedelta(days=body.duration_days)

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO prismrag.lib_license
                (license_key_hash, license_key_prefix, company_name, contact_email,
                 plan, expires_at, stripe_subscription_id, stripe_customer_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            key_hash, prefix, body.company_name, body.contact_email,
            body.plan, expires_at,
            body.stripe_subscription_id, body.stripe_customer_id,
        ))
        lid = cur.fetchone()[0]
        conn.commit()
        log.info("License issued: %s for %s (%s)", prefix, body.company_name, body.plan)
        return {
            "id":          str(lid),
            "license_key": raw_key,   # shown ONCE — customer must save this
            "prefix":      prefix,
            "company":     body.company_name,
            "plan":        body.plan,
            "expires_at":  expires_at.isoformat(),
        }
    finally:
        release_conn(conn)


# ── Admin: list licenses ──────────────────────────────────────────────────────

@router.get("/licenses")
def list_licenses(_admin: dict = Depends(_require_superadmin)):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, license_key_prefix, company_name, contact_email,
                   plan, status, issued_at, expires_at, calls_today,
                   last_validated_at
            FROM prismrag.lib_license
            ORDER BY issued_at DESC
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        release_conn(conn)


# ── Admin: get one license ────────────────────────────────────────────────────

@router.get("/licenses/{license_id}")
def get_license(license_id: str, _admin: dict = Depends(_require_superadmin)):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, license_key_prefix, company_name, contact_email,
                   plan, status, issued_at, expires_at, calls_today,
                   max_calls_per_day, stripe_subscription_id, last_validated_at
            FROM prismrag.lib_license WHERE id = %s
        """, (license_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="License not found")
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        release_conn(conn)


# ── Admin: update status ──────────────────────────────────────────────────────

@router.put("/licenses/{license_id}/status")
def update_license_status(
    license_id: str,
    body: UpdateStatusRequest,
    _admin: dict = Depends(_require_superadmin),
):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE prismrag.lib_license
            SET status = %s, updated_at = now()
            WHERE id = %s
            RETURNING id, status
        """, (body.status, license_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="License not found")
        conn.commit()
        return {"id": str(row[0]), "status": row[1]}
    finally:
        release_conn(conn)
