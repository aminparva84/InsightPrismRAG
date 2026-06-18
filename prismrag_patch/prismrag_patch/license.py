"""
License validation for prismrag-patch.

Validates against https://prismrag.insightits.com/api/v1/lib/validate.
- First call: hits the API, caches result locally for 23 hours.
- Offline: 7-day grace period before raising LicenseError.
- Grace period exceeded: raises LicenseError with a clear message.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

VALIDATE_URL = os.getenv(
    "PRISMRAG_LICENSE_URL",
    "https://prismrag.insightits.com/api/v1/lib/validate",
)
CACHE_TTL_SECONDS  = 23 * 3600        # re-validate every 23 hours
GRACE_PERIOD_DAYS  = 7                 # offline grace period


class LicenseError(RuntimeError):
    """Raised when the license key is invalid, expired, or revoked."""


def _cache_path(key: str) -> Path:
    slug = hashlib.sha256(key.encode()).hexdigest()[:16]
    base = Path(os.getenv("PRISMRAG_CACHE_DIR", Path.home() / ".cache" / "prismrag_patch"))
    base.mkdir(parents=True, exist_ok=True)
    return base / f"lic_{slug}.json"


def _read_cache(key: str) -> Optional[dict]:
    try:
        data = json.loads(_cache_path(key).read_text())
        if time.time() - data.get("cached_at", 0) < CACHE_TTL_SECONDS:
            return data
        return None  # stale
    except Exception:
        return None


def _write_cache(key: str, payload: dict) -> None:
    try:
        payload["cached_at"] = time.time()
        _cache_path(key).write_text(json.dumps(payload))
    except Exception:
        pass


def _last_valid_at(key: str) -> Optional[float]:
    """Return timestamp of last successful validation, or None."""
    try:
        data = json.loads(_cache_path(key).read_text())
        return data.get("validated_at")
    except Exception:
        return None


def validate_license(key: str, product: str = "prismrag-patch") -> dict:
    """
    Validate *key* against the PrismRAG license server.

    Returns the license metadata dict on success.
    Raises LicenseError on failure.
    """
    if not key or not key.startswith("prlib_"):
        raise LicenseError(
            "Invalid license key format. Keys start with 'prlib_'. "
            "Get yours at https://prismrag.insightits.com/prismrag-lib.html"
        )

    # 1. Check cache
    cached = _read_cache(key)
    if cached:
        if cached.get("valid"):
            log.debug("prismrag-patch: license valid (cached)")
            return cached
        raise LicenseError(cached.get("message", "License invalid (cached)"))

    # 2. Call API
    try:
        resp = requests.post(
            VALIDATE_URL,
            json={"license_key": key, "product": product},
            timeout=8,
        )
        data = resp.json()
    except requests.RequestException as exc:
        # Offline — check grace period
        last = _last_valid_at(key)
        if last and (time.time() - last) < GRACE_PERIOD_DAYS * 86400:
            log.warning("prismrag-patch: offline, using grace period (%d days left)",
                        GRACE_PERIOD_DAYS - int((time.time() - last) / 86400))
            return {"valid": True, "offline_grace": True}
        raise LicenseError(
            f"Cannot reach license server and grace period exceeded. "
            f"Check your internet connection or contact prismrag@insightits.com. ({exc})"
        ) from exc

    if not data.get("valid"):
        _write_cache(key, {**data, "validated_at": time.time()})
        raise LicenseError(data.get("message", "License key rejected by server."))

    data["validated_at"] = time.time()
    _write_cache(key, data)
    log.debug("prismrag-patch: license valid (server confirmed)")
    return data
