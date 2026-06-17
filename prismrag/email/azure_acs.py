"""PrismRAG — Transactional email via Azure Communication Services."""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

ACS_CONNECTION_STRING = os.getenv("AZURE_COMMUNICATION_CONNECTION_STRING", "")
EMAIL_FROM = os.getenv(
    "PRISMRAG_EMAIL_FROM",
    os.getenv("AZURE_EMAIL_FROM", "PrismRAG@insightits.com"),
)
EMAIL_ENABLED = os.getenv("PRISMRAG_EMAIL_ENABLED", "true").lower() in ("1", "true", "yes")


def _log_email(to: str, subject: str, template: str, status: str, msg_id: str | None = None, err: str | None = None):
    def _write():
        from prismrag.db import get_conn, release_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO prismrag.email_log
                    (to_address, subject, template, status, provider_msg_id, error_message)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (to, subject, template, status, msg_id, err),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            release_conn(conn)

    threading.Thread(target=_write, daemon=True).start()


def send_email(
    to: str,
    subject: str,
    html_body: str,
    plain_body: str | None = None,
    template: str = "generic",
) -> dict:
    """
    Send email via Azure Communication Services Email.
    Requires domain verification: insightits.com DNS in AWS Route 53 → Azure ACS records.
    """
    if not EMAIL_ENABLED:
        logger.info("Email disabled — would send to %s: %s", to, subject)
        _log_email(to, subject, template, "skipped")
        return {"status": "skipped", "reason": "PRISMRAG_EMAIL_ENABLED=false"}

    if not ACS_CONNECTION_STRING:
        logger.warning("AZURE_COMMUNICATION_CONNECTION_STRING not set")
        _log_email(to, subject, template, "failed", err="ACS not configured")
        return {"status": "failed", "reason": "Azure Communication Services not configured"}

    try:
        from azure.communication.email import EmailClient
    except ImportError:
        _log_email(to, subject, template, "failed", err="azure-communication-email not installed")
        return {"status": "failed", "reason": "pip install azure-communication-email"}

    message = {
        "senderAddress": EMAIL_FROM,
        "recipients": {"to": [{"address": to}]},
        "content": {
            "subject": subject,
            "html": html_body,
            "plainText": plain_body or _strip_html(html_body),
        },
    }

    try:
        client = EmailClient.from_connection_string(ACS_CONNECTION_STRING)
        poller = client.begin_send(message)
        result = poller.result()
        msg_id = getattr(result, "message_id", None) or str(result)
        _log_email(to, subject, template, "sent", msg_id=msg_id)
        return {"status": "sent", "message_id": msg_id}
    except Exception as exc:
        logger.exception("ACS email failed to %s", to)
        _log_email(to, subject, template, "failed", err=str(exc)[:500])
        return {"status": "failed", "reason": str(exc)}


def send_welcome_email(to: str, full_name: str) -> dict:
    name = full_name or "there"
    html = f"""
    <html><body style="font-family:Inter,sans-serif;color:#1a1a2e;">
    <h2>Welcome to PrismRAG</h2>
    <p>Hi {name},</p>
    <p>Your account is ready. PrismRAG lets you define Tier-1 mapping rules and search
    your knowledge graph with Graph RAG retrieval.</p>
    <p><a href="https://prismrag.insightits.com/dashboard.html">Open dashboard</a></p>
    <p style="color:#666;font-size:12px;">PrismRAG by Insight ITS — PrismRAG@insightits.com</p>
    </body></html>
    """
    return send_email(to, "Welcome to PrismRAG", html, template="welcome")


def send_mfa_enabled_email(to: str) -> dict:
    html = """
    <html><body style="font-family:Inter,sans-serif;">
    <h2>MFA enabled on your PrismRAG account</h2>
    <p>Two-factor authentication is now active. If you did not enable this, contact support immediately.</p>
    </body></html>
    """
    return send_email(to, "PrismRAG — MFA enabled", html, template="mfa_enabled")


def send_password_reset_email(to: str, reset_url: str) -> dict:
    html = f"""
    <html><body style="font-family:Inter,sans-serif;">
    <h2>Reset your PrismRAG password</h2>
    <p><a href="{reset_url}">Click here to reset</a> (expires in 1 hour).</p>
    </body></html>
    """
    return send_email(to, "PrismRAG — Password reset", html, template="password_reset")


def send_member_invite_email(to: str, org_name: str, inviter: str) -> dict:
    html = f"""
    <html><body style="font-family:Inter,sans-serif;">
    <h2>You've been invited to {org_name} on PrismRAG</h2>
    <p>{inviter} added you to a workspace. <a href="https://prismrag.insightits.com/login.html">Sign in</a> to get started.</p>
    </body></html>
    """
    return send_email(to, f"PrismRAG invitation — {org_name}", html, template="member_invite")


def _strip_html(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html)
