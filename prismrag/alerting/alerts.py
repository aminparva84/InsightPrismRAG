"""
PrismRAG alerting strategy.

Admin alerts  → all addresses in PRISMRAG_ADMIN_EMAILS (comma-separated)
Client alerts → user's registered email, only for errors they triggered
Severity      → CRITICAL (page + email), ERROR (email), WARNING (log only)

Environment variables:
  PRISMRAG_ADMIN_EMAILS   Comma-separated admin email list
                          Default: prismrag@insightits.com
  PRISMRAG_ALERT_MIN_SEVERITY  Minimum severity to send admin email (WARNING/ERROR/CRITICAL)
                               Default: ERROR
"""
from __future__ import annotations

import logging
import os
import threading
import traceback
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

ADMIN_EMAILS: list[str] = [
    e.strip()
    for e in os.getenv(
        "PRISMRAG_ADMIN_EMAILS",
        "prismrag@insightits.com",
    ).split(",")
    if e.strip()
]

_SEVERITY_RANK = {"WARNING": 1, "ERROR": 2, "CRITICAL": 3}
_MIN_SEVERITY  = _SEVERITY_RANK.get(
    os.getenv("PRISMRAG_ALERT_MIN_SEVERITY", "ERROR").upper(), 2
)


class ErrorSeverity(str, Enum):
    WARNING  = "WARNING"
    ERROR    = "ERROR"
    CRITICAL = "CRITICAL"


# ── Admin alert ───────────────────────────────────────────────────────────────

def alert_admin(
    subject: str,
    message: str,
    severity: ErrorSeverity = ErrorSeverity.ERROR,
    exc: Optional[BaseException] = None,
    context: Optional[dict] = None,
) -> None:
    """
    Fire-and-forget admin alert email. Always logs; sends email when severity
    meets PRISMRAG_ALERT_MIN_SEVERITY threshold.
    """
    rank = _SEVERITY_RANK.get(severity.value, 2)
    log_fn = logger.critical if rank >= 3 else logger.error if rank >= 2 else logger.warning
    log_fn("[ALERT][%s] %s — %s", severity.value, subject, message)

    if rank < _MIN_SEVERITY:
        return

    threading.Thread(
        target=_send_admin_emails,
        args=(subject, message, severity, exc, context),
        daemon=True,
    ).start()


def _send_admin_emails(
    subject: str,
    message: str,
    severity: ErrorSeverity,
    exc: Optional[BaseException],
    context: Optional[dict],
) -> None:
    from prismrag.email.azure_acs import send_email

    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else ""
    ctx_rows = "".join(
        f"<tr><td style='padding:4px 8px;color:#666;'>{k}</td>"
        f"<td style='padding:4px 8px;font-family:monospace;'>{v}</td></tr>"
        for k, v in (context or {}).items()
    )
    color = {"WARNING": "#f59e0b", "ERROR": "#ef4444", "CRITICAL": "#7c3aed"}.get(severity.value, "#ef4444")

    html = f"""
    <html><body style="font-family:Inter,Arial,sans-serif;background:#f8fafc;padding:24px;">
    <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:8px;
                border-left:5px solid {color};padding:24px;box-shadow:0 2px 8px rgba(0,0,0,.08);">
      <h2 style="margin:0 0 8px;color:{color};">[{severity.value}] PrismRAG Alert</h2>
      <h3 style="margin:0 0 16px;color:#1a1a2e;">{subject}</h3>
      <p style="color:#374151;">{message}</p>
      {"<table style='border-collapse:collapse;width:100%;margin-top:16px;'>" + ctx_rows + "</table>" if ctx_rows else ""}
      {"<pre style='background:#1a1a2e;color:#e2e8f0;padding:16px;border-radius:6px;"
       "font-size:12px;overflow:auto;margin-top:16px;'>" + tb + "</pre>" if tb else ""}
      <hr style="margin:24px 0;border:none;border-top:1px solid #e5e7eb;">
      <p style="color:#9ca3af;font-size:12px;margin:0;">
        PrismRAG · <a href="https://prismrag.insightits.com">prismrag.insightits.com</a>
      </p>
    </div></body></html>
    """

    for admin in ADMIN_EMAILS:
        result = send_email(
            to=admin,
            subject=f"[PrismRAG {severity.value}] {subject}",
            html_body=html,
            template="admin_alert",
        )
        if result.get("status") != "sent":
            logger.error("Admin alert email to %s failed: %s", admin, result)


# ── Client-facing apology email ───────────────────────────────────────────────

def alert_client(
    to: str,
    user_name: str,
    operation: str,
    support_ref: Optional[str] = None,
) -> None:
    """
    Send a polite apology email to the affected client. Fire-and-forget.
    Call this alongside alert_admin when an error directly impacts a user's request.
    """
    threading.Thread(
        target=_send_client_apology,
        args=(to, user_name, operation, support_ref),
        daemon=True,
    ).start()


def _send_client_apology(
    to: str,
    user_name: str,
    operation: str,
    support_ref: Optional[str],
) -> None:
    from prismrag.email.azure_acs import send_email

    ref_line = (
        f"<p style='color:#6b7280;font-size:13px;'>Reference: <code>{support_ref}</code></p>"
        if support_ref else ""
    )
    name = user_name or "there"

    html = f"""
    <html><body style="font-family:Inter,Arial,sans-serif;background:#f8fafc;padding:24px;">
    <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:8px;
                padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08);">
      <h2 style="margin:0 0 16px;color:#1a1a2e;">We're sorry, {name}</h2>
      <p style="color:#374151;line-height:1.6;">
        We encountered an unexpected error while processing your <strong>{operation}</strong> request.
        Our team has been notified automatically and is looking into it.
      </p>
      <p style="color:#374151;line-height:1.6;">
        No action is needed from you. If this keeps happening, please reach out to us at
        <a href="mailto:prismrag@insightits.com">prismrag@insightits.com</a>
        and we'll prioritise a fix for you.
      </p>
      {ref_line}
      <hr style="margin:24px 0;border:none;border-top:1px solid #e5e7eb;">
      <p style="color:#9ca3af;font-size:12px;margin:0;">
        PrismRAG by Insight ITS ·
        <a href="https://prismrag.insightits.com">prismrag.insightits.com</a>
      </p>
    </div></body></html>
    """

    result = send_email(
        to=to,
        subject="We're sorry — PrismRAG encountered an error",
        html_body=html,
        template="client_apology",
    )
    if result.get("status") != "sent":
        logger.error("Client apology email to %s failed: %s", to, result)
