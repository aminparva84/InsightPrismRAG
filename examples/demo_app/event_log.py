"""Shared event logging for the demo app."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"


def init_event_log(run_name: str = "demo") -> tuple[logging.Logger, Path]:
    """Create logs/<run_name>_<timestamp>.log and return logger + path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"{run_name}_{ts}.log"

    logger = logging.getLogger("prismrag_demo")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    logger.info("=== PrismRAG demo event log started ===")
    logger.info("Log file: %s", log_path)
    return logger, log_path


def log_event(logger: logging.Logger, event: str, **details) -> None:
    """Log a named event with optional structured details."""
    if details:
        payload = json.dumps(details, default=str, ensure_ascii=False)
        logger.info("EVENT %s | %s", event, payload)
    else:
        logger.info("EVENT %s", event)
