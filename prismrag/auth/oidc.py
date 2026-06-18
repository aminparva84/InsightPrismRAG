"""PrismRAG — OpenID Connect (SSO) integration."""
from __future__ import annotations

import os
import secrets
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx

OIDC_ISSUER = (os.getenv("OIDC_ISSUER") or os.getenv("PRISMRAG_OIDC_ISSUER") or "").rstrip("/")
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID") or os.getenv("PRISMRAG_OIDC_CLIENT_ID") or ""
OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET") or os.getenv("PRISMRAG_OIDC_CLIENT_SECRET") or ""
OIDC_REDIRECT_URI = os.getenv("OIDC_REDIRECT_URI") or os.getenv("PRISMRAG_OIDC_REDIRECT_URI") or ""
OIDC_SCOPES = os.getenv("OIDC_SCOPES", "openid email profile")

_STATE_TTL = 600  # 10 minutes


def _redis():
    """Return a Redis client if REDIS_URL is configured, else None."""
    url = os.getenv("REDIS_URL") or os.getenv("PRISMRAG_REDIS_URL")
    if not url:
        return None
    try:
        import redis
        return redis.from_url(url, decode_responses=True, socket_timeout=2)
    except Exception:
        return None


# Fallback in-memory store — used only when Redis is unavailable (single-instance dev)
_pending_states: dict[str, float] = {}


def oidc_enabled() -> bool:
    return bool(OIDC_ISSUER and OIDC_CLIENT_ID and OIDC_REDIRECT_URI)


def _discovery() -> dict[str, Any]:
    url = f"{OIDC_ISSUER}/.well-known/openid-configuration"
    with httpx.Client(timeout=15.0) as client:
        res = client.get(url)
        res.raise_for_status()
        return res.json()


def build_authorize_url() -> tuple[str, str]:
    """Return (authorize_url, state)."""
    if not oidc_enabled():
        raise RuntimeError("OIDC is not configured")

    meta = _discovery()
    state = secrets.token_urlsafe(32)

    r = _redis()
    if r:
        r.setex(f"oidc:state:{state}", _STATE_TTL, "1")
    else:
        import time as _time
        _pending_states[state] = _time.time()

    params = {
        "client_id": OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": OIDC_SCOPES,
        "redirect_uri": OIDC_REDIRECT_URI,
        "state": state,
    }
    url = meta["authorization_endpoint"] + "?" + urlencode(params)
    return url, state


def consume_state(state: str, max_age: int = _STATE_TTL) -> bool:
    r = _redis()
    if r:
        key = f"oidc:state:{state}"
        deleted = r.delete(key)
        return deleted == 1

    import time
    ts = _pending_states.pop(state, None)
    if ts is None:
        return False
    return time.time() - ts <= max_age


def exchange_code(code: str) -> dict[str, Any]:
    """Exchange authorization code for tokens; return claims from id_token."""
    if not oidc_enabled():
        raise RuntimeError("OIDC is not configured")

    meta = _discovery()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": OIDC_REDIRECT_URI,
        "client_id": OIDC_CLIENT_ID,
        "client_secret": OIDC_CLIENT_SECRET,
    }
    with httpx.Client(timeout=15.0) as client:
        res = client.post(meta["token_endpoint"], data=data)
        res.raise_for_status()
        tokens = res.json()

    id_token = tokens.get("id_token")
    if not id_token:
        raise ValueError("No id_token in OIDC response")

    import jwt
    # Decode without verify first to get kid, then verify with JWKS
    unverified = jwt.get_unverified_header(id_token)
    jwks_uri = meta.get("jwks_uri", f"{OIDC_ISSUER}/.well-known/jwks.json")
    with httpx.Client(timeout=15.0) as client:
        jwks = client.get(jwks_uri).json()

    from jwt import PyJWKClient
    jwk_client = PyJWKClient(jwks_uri)
    signing_key = jwk_client.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=[unverified.get("alg", "RS256")],
        audience=OIDC_CLIENT_ID,
        issuer=meta.get("issuer", OIDC_ISSUER),
    )
    return claims


def find_or_create_user(claims: dict[str, Any]) -> dict[str, Any]:
    """Link OIDC identity to user_account; create if new."""
    from prismrag.db import get_conn, release_conn

    sub = claims.get("sub", "")
    email = (claims.get("email") or "").lower()
    name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0]
    provider = "default"

    if not sub or not email:
        raise ValueError("OIDC token missing sub or email")

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id FROM prismrag.oidc_identity
            WHERE provider = %s AND subject = %s
            """,
            (provider, sub),
        )
        row = cur.fetchone()
        if row:
            user_id = str(row[0])
        else:
            cur.execute(
                "SELECT id FROM prismrag.user_account WHERE email = %s",
                (email,),
            )
            existing = cur.fetchone()
            if existing:
                user_id = str(existing[0])
            else:
                user_id = str(uuid.uuid4())
                cur.execute(
                    """
                    INSERT INTO prismrag.user_account
                        (id, email, password_hash, full_name, plan, email_verified)
                    VALUES (%s, %s, NULL, %s, 'free', TRUE)
                    """,
                    (user_id, email, name),
                )
                tenant_id = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO prismrag.tenant (id, name, owner_email) VALUES (%s, %s, %s)",
                    (tenant_id, name or "My Workspace", email),
                )
                cur.execute(
                    """
                    INSERT INTO prismrag.tenant_member (tenant_id, user_id, role)
                    VALUES (%s, %s, 'owner')
                    ON CONFLICT DO NOTHING
                    """,
                    (tenant_id, user_id),
                )

            cur.execute(
                """
                INSERT INTO prismrag.oidc_identity (user_id, provider, subject, email)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (provider, subject) DO UPDATE SET email = EXCLUDED.email
                """,
                (user_id, provider, sub, email),
            )

        cur.execute(
            """
            SELECT id, email, full_name, company, plan,
                   subscription_status, is_active, stripe_customer_id
            FROM prismrag.user_account WHERE id = %s
            """,
            (user_id,),
        )
        u = cur.fetchone()
        conn.commit()
    finally:
        release_conn(conn)

    if not u or not u[6]:
        raise ValueError("User account inactive")

    return {
        "id": str(u[0]),
        "email": u[1],
        "fullName": u[2],
        "company": u[3],
        "plan": u[4],
        "subscriptionStatus": u[5],
        "stripeCustomerId": u[7],
    }
