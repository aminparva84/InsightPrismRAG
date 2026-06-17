"""PrismRAG — Password reset tokens (email link, 1-hour TTL)."""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

RESET_TTL_HOURS = int(os.getenv("PRISMRAG_RESET_TTL_HOURS", "1"))


def create_reset_token(user_id: str) -> str:
    """Create a one-time reset token; returns raw token for the email link."""
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(hours=RESET_TTL_HOURS)

    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM prismrag.password_reset_token WHERE user_id = %s OR expires_at < now()",
            (user_id,),
        )
        cur.execute(
            """
            INSERT INTO prismrag.password_reset_token (id, user_id, token_hash, expires_at)
            VALUES (%s, %s, %s, %s)
            """,
            (str(uuid.uuid4()), user_id, token_hash, expires),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return raw


def consume_reset_token(raw: str) -> str:
    """Validate token and return user_id; marks token used."""
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM prismrag.password_reset_token
            WHERE token_hash = %s AND used_at IS NULL AND expires_at > now()
            RETURNING user_id::text
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        conn.commit()
    finally:
        release_conn(conn)

    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")
    return row[0]


def reset_base_url() -> str:
    return os.getenv("PRISMRAG_BASE_URL", "http://localhost:8001").rstrip("/")
