"""PrismRAG — TOTP MFA (RFC 6238)."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

MFA_ISSUER = "PrismRAG"
CHALLENGE_TTL_MINUTES = 5


def generate_totp_secret() -> str:
    try:
        import pyotp
        return pyotp.random_base32()
    except ImportError:
        raise RuntimeError("pyotp required: pip install pyotp")


def totp_uri(secret: str, email: str) -> str:
    import pyotp
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=MFA_ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    import pyotp
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def generate_backup_codes(count: int = 8) -> list[str]:
    return [secrets.token_hex(4).upper() for _ in range(count)]


def hash_backup_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def verify_backup_code(user_id: str, code: str) -> bool:
    from prismrag.db import get_conn, release_conn

    code_hash = hash_backup_code(code)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT mfa_backup_codes FROM prismrag.user_account WHERE id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return False
        hashes = list(row[0] or [])
        if code_hash not in hashes:
            return False
        hashes.remove(code_hash)
        cur.execute(
            "UPDATE prismrag.user_account SET mfa_backup_codes = %s WHERE id = %s",
            (hashes, user_id),
        )
        conn.commit()
        return True
    finally:
        release_conn(conn)


def create_mfa_challenge(user_id: str) -> str:
    """Return opaque challenge token for step-2 login."""
    from prismrag.db import get_conn, release_conn

    raw = secrets.token_urlsafe(32)
    ch_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(minutes=CHALLENGE_TTL_MINUTES)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM prismrag.mfa_challenge WHERE user_id = %s OR expires_at < now()",
            (user_id,),
        )
        cur.execute(
            """
            INSERT INTO prismrag.mfa_challenge (id, user_id, challenge_hash, expires_at)
            VALUES (%s, %s, %s, %s)
            """,
            (str(uuid.uuid4()), user_id, ch_hash, expires),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return raw


def consume_mfa_challenge(challenge_token: str) -> str:
    """Validate challenge and return user_id."""
    from prismrag.db import get_conn, release_conn

    ch_hash = hashlib.sha256(challenge_token.encode()).hexdigest()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM prismrag.mfa_challenge
            WHERE challenge_hash = %s AND expires_at > now()
            RETURNING user_id::text
            """,
            (ch_hash,),
        )
        row = cur.fetchone()
        conn.commit()
    finally:
        release_conn(conn)

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA challenge")
    return row[0]


def get_mfa_status(user_id: str) -> dict:
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT mfa_enabled, mfa_secret IS NOT NULL FROM prismrag.user_account WHERE id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    finally:
        release_conn(conn)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"mfa_enabled": bool(row[0]), "mfa_configured": bool(row[1])}
