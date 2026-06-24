"""Configure application logging for production (stdout + optional blob files)."""
from __future__ import annotations

import logging
import os
import sys

from prismrag.logging.blob_handler import BlobLogHandler

_CONFIGURED = False


def configure_logging(service: str = "api") -> None:
    """
    Idempotent logging setup.

    - PRISMRAG_LOG_LEVEL: DEBUG|INFO|WARNING|ERROR (default INFO, use WARNING in prod)
    - PRISMRAG_LOG_BLOB_ENABLED: true → flush logs to Azure Blob (see blob_handler.py)
    - Errors still trigger alert_admin emails via PRISMRAG_ADMIN_EMAILS
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    level_name = os.getenv("PRISMRAG_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = "%(asctime)s %(levelname)s %(name)s — %(message)s"
    formatter = logging.Formatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)

    # Replace any prior basicConfig handlers
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(level)
    root.addHandler(console)

    blob = BlobLogHandler()
    blob.setFormatter(formatter)
    blob.setLevel(level)
    if blob.enabled:
        root.addHandler(blob)
        root.info(
            "Blob log sink enabled (container=%s, service=%s)",
            os.getenv("PRISMRAG_LOG_BLOB_CONTAINER", "prismrag-logs"),
            service,
        )

    # Quieter third-party loggers in production
    if os.getenv("PRISMRAG_ENV", "").lower() == "production":
        for name in ("urllib3", "httpx", "httpcore", "azure", "stripe"):
            logging.getLogger(name).setLevel(logging.WARNING)
