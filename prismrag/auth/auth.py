"""PrismRAG — Authentication: JWT + bcrypt + API key management."""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

JWT_SECRET  = (
    os.getenv("PRISMRAG_JWT_SECRET")
    or os.getenv("JWT_SECRET")
    or ""
)
if not JWT_SECRET:
    _env = (os.getenv("PRISMRAG_ENV") or os.getenv("ENVIRONMENT") or "development").lower()
    if _env in ("production", "prod", "staging"):
        raise RuntimeError(
            "JWT_SECRET or PRISMRAG_JWT_SECRET must be set in production/staging. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    JWT_SECRET = secrets.token_hex(32)
JWT_ALGO    = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("PRISMRAG_JWT_EXPIRE_HOURS", "72"))

_bearer = HTTPBearer(auto_error=False)


# ── Password ──────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        # Fallback: PBKDF2 (no bcrypt installed)
        salt = secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 260000)
        return f"pbkdf2:{salt}:{dk.hex()}"


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        if hashed.startswith("pbkdf2:"):
            _, salt, dk_hex = hashed.split(":", 2)
            dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 260000)
            return secrets.compare_digest(dk.hex(), dk_hex)
        import bcrypt
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_jwt(user_id: str, email: str, plan: str) -> str:
    try:
        import jwt
    except ImportError:
        raise RuntimeError("PyJWT required: pip install PyJWT")

    payload = {
        "sub":   user_id,
        "email": email,
        "plan":  plan,
        "iat":   datetime.now(timezone.utc),
        "exp":   datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_jwt(token: str) -> dict[str, Any]:
    try:
        import jwt
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        )


# ── API keys ──────────────────────────────────────────────────────────────────

def generate_api_key() -> tuple[str, str, str]:
    """Generate (raw_key, key_hash, key_prefix). Store hash only."""
    raw    = "prk_" + secrets.token_urlsafe(32)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12]
    return raw, hashed, prefix


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── FastAPI dependency ────────────────────────────────────────────────────────

def check_api_scope(user: dict, scope: str) -> None:
    """Raise 403 if request used an API key without the required scope."""
    scopes = user.get("_apiKeyScopes")
    if scopes is None:
        return  # JWT session — full access
    if scope not in scopes and "admin" not in scopes:
        raise HTTPException(
            status_code=403,
            detail=f"API key missing scope '{scope}'. Create a key with read,write scopes.",
        )


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict:
    """
    Resolves auth from either:
      - Bearer JWT token  (Authorization: Bearer <jwt>)
      - Bearer API key    (Authorization: Bearer prk_...)
    Returns user dict from DB.
    """
    if not creds:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = creds.credentials

    if token.startswith("prk_"):
        return _resolve_api_key(token)
    return _resolve_jwt(token)


def _resolve_jwt(token: str) -> dict:
    payload = decode_jwt(token)
    return _load_user(payload["sub"])


def _resolve_api_key(raw: str) -> dict:
    key_hash = hash_api_key(raw)
    from prismrag.db import get_conn, release_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ak.user_id, ak.id, ak.is_active, ak.scopes
            FROM prismrag.api_key ak
            WHERE ak.key_hash = %s
            """,
            (key_hash,),
        )
        row = cur.fetchone()
        if not row or not row[2]:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        # Update last_used_at
        cur.execute(
            "UPDATE prismrag.api_key SET last_used_at = now() WHERE id = %s",
            (row[1],),
        )
        conn.commit()
        user = _load_user(str(row[0]), conn=None)
        user["_apiKeyScopes"] = list(row[3] or ["read", "write"])
        return user
    finally:
        release_conn(conn)


def _load_user(user_id: str, conn=None) -> dict:
    from prismrag.db import get_conn, release_conn
    _conn = conn or get_conn()
    owned = conn is None
    try:
        cur = _conn.cursor()
        cur.execute(
            """
            SELECT id, email, full_name, company, plan,
                   subscription_status, is_active, stripe_customer_id
            FROM prismrag.user_account WHERE id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if not row or not row[6]:
            raise HTTPException(status_code=401, detail="User not found or inactive")
        return {
            "id":                  str(row[0]),
            "email":               row[1],
            "fullName":            row[2],
            "company":             row[3],
            "plan":                row[4],
            "subscriptionStatus":  row[5],
            "stripeCustomerId":    row[7],
        }
    finally:
        if owned:
            release_conn(_conn)


def require_plan(*plans: str):
    """FastAPI dependency factory: require user to be on one of the given plans."""
    def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user["plan"] not in plans and "enterprise" not in plans:
            raise HTTPException(
                status_code=403,
                detail=f"This feature requires one of: {', '.join(plans)}. "
                       f"Your plan: {user['plan']}. Upgrade at /billing/portal",
            )
        return user
    return _dep
