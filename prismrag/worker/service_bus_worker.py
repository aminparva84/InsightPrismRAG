"""PrismRAG — Azure Service Bus consumer for large-file ingest jobs."""
from __future__ import annotations

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

QUEUE = os.getenv("AZURE_SERVICE_BUS_QUEUE", "prismrag-jobs")


def _process_message(body: dict) -> None:
    from prismrag.worker.large_file import process_large_file

    upload_id = body.get("upload_id")
    if not upload_id:
        raise ValueError("Message missing upload_id")
    process_large_file(upload_id)


def run_forever() -> None:
    logging.basicConfig(level=logging.INFO)
    conn_str = os.getenv("AZURE_SERVICE_BUS_CONNECTION_STRING")
    if not conn_str:
        logger.error("AZURE_SERVICE_BUS_CONNECTION_STRING not set")
        sys.exit(1)

    try:
        from azure.servicebus import ServiceBusClient
    except ImportError:
        logger.error("azure-servicebus not installed")
        sys.exit(1)

    logger.info("Service Bus worker listening on queue: %s", QUEUE)
    with ServiceBusClient.from_connection_string(conn_str) as client:
        receiver = client.get_queue_receiver(queue_name=QUEUE)
        with receiver:
            for msg in receiver:
                try:
                    body = json.loads(str(msg))
                    _process_message(body)
                    receiver.complete_message(msg)
                    logger.info("Processed upload_id=%s", body.get("upload_id"))
                except Exception as exc:
                    logger.exception("Failed to process message: %s", exc)
                    receiver.abandon_message(msg)


if __name__ == "__main__":
    from prismrag.db import init_schema
    init_schema()
    run_forever()
