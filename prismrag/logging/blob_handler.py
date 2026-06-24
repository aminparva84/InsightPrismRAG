"""
Buffered logging handler — flushes log lines to Azure Blob Storage as plain text files.

Much cheaper than Log Analytics (~$0.02/GB/mo vs ~$2.76/GB ingested).
Path layout: logs/YYYY-MM-DD/{host}/{timestamp}.log
"""
from __future__ import annotations

import atexit
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone

_HOST = os.getenv("HOSTNAME", os.getenv("CONTAINER_APP_REVISION", socket.gethostname()))
_CONTAINER = os.getenv("PRISMRAG_LOG_BLOB_CONTAINER", "prismrag-logs")
_FLUSH_SEC = float(os.getenv("PRISMRAG_LOG_BLOB_FLUSH_SEC", "300"))
_MAX_BUFFER = int(os.getenv("PRISMRAG_LOG_BLOB_MAX_BYTES", "262144"))  # 256 KB


def _connection_string() -> str | None:
    direct = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if direct:
        return direct
    account = os.getenv("AZURE_STORAGE_ACCOUNT", "").strip()
    key = os.getenv("AZURE_STORAGE_KEY", "").strip()
    if account and key:
        return (
            f"DefaultEndpointsProtocol=https;AccountName={account};"
            f"AccountKey={key};EndpointSuffix=core.windows.net"
        )
    return None


class BlobLogHandler(logging.Handler):
    """Accumulates formatted log records; uploads a blob on flush interval or buffer size."""

    def __init__(self) -> None:
        super().__init__()
        self._buf: list[str] = []
        self._buf_bytes = 0
        self._lock = threading.Lock()
        self._conn_str = _connection_string()
        self._enabled = (
            os.getenv("PRISMRAG_LOG_BLOB_ENABLED", "false").lower()
            in ("1", "true", "yes", "on")
            and bool(self._conn_str)
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if self._enabled:
            self._thread = threading.Thread(
                target=self._flush_loop, name="blob-log-flush", daemon=True
            )
            self._thread.start()
            atexit.register(self.close)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def emit(self, record: logging.LogRecord) -> None:
        if not self._enabled:
            return
        try:
            line = self.format(record) + "\n"
            with self._lock:
                self._buf.append(line)
                self._buf_bytes += len(line.encode("utf-8", errors="replace"))
                if self._buf_bytes >= _MAX_BUFFER or record.levelno >= logging.ERROR:
                    self._upload_locked()
        except Exception:
            pass  # logging must never crash the app

    def _flush_loop(self) -> None:
        while not self._stop.wait(_FLUSH_SEC):
            with self._lock:
                self._upload_locked()

    def _upload_locked(self) -> None:
        if not self._buf:
            return
        body = "".join(self._buf)
        self._buf.clear()
        self._buf_bytes = 0
        threading.Thread(
            target=self._upload_blob,
            args=(body,),
            name="blob-log-upload",
            daemon=True,
        ).start()

    def _upload_blob(self, body: str) -> None:
        try:
            from azure.storage.blob import BlobServiceClient

            now = datetime.now(timezone.utc)
            date = now.strftime("%Y-%m-%d")
            ts = now.strftime("%H%M%S_%f")
            blob_name = f"logs/{date}/{_HOST}/{ts}.log"

            client = BlobServiceClient.from_connection_string(self._conn_str)  # type: ignore[arg-type]
            container = client.get_container_client(_CONTAINER)
            try:
                container.create_container()
            except Exception:
                pass  # already exists
            blob = container.get_blob_client(blob_name)
            blob.upload_blob(body.encode("utf-8"), overwrite=True)
        except Exception as exc:
            # Last resort — stderr only; avoid recursive logging
            import sys

            print(f"[blob-log] upload failed: {exc}", file=sys.stderr)

    def flush(self) -> None:
        with self._lock:
            self._upload_locked()

    def close(self) -> None:
        self._stop.set()
        self.flush()
        super().close()
