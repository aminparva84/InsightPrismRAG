"""PrismRAG worker — Postgres job queue + Service Bus large-file ingest."""
from __future__ import annotations

import logging
import os
import sys
import threading

logger = logging.getLogger(__name__)


def _start_service_bus_worker() -> None:
    conn_str = os.getenv("AZURE_SERVICE_BUS_CONNECTION_STRING", "")
    if not conn_str or conn_str == "not-configured":
        logger.info("Service Bus not configured — large-file queue disabled")
        return
    from prismrag.worker.service_bus_worker import run_forever

    run_forever()


def main() -> None:
    from prismrag.logging import configure_logging

    configure_logging(service="worker")
    from prismrag.db import init_schema

    init_schema()

    sb = threading.Thread(target=_start_service_bus_worker, name="service-bus", daemon=True)
    sb.start()

    from prismrag.worker.job_worker import run_forever

    logger.info("Starting Postgres job worker (inline/API ingest)")
    run_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
