"""PrismRAG — Postgres-backed async job worker."""
from __future__ import annotations

import json
import logging
import os
import socket
import time
import uuid

logger = logging.getLogger(__name__)

WORKER_ID = os.getenv("PRISMRAG_WORKER_ID", socket.gethostname())
POLL_INTERVAL = float(os.getenv("PRISMRAG_WORKER_POLL_SEC", "2"))


def enqueue_job(
    job_id: str,
    tenant_id: str,
    request_dict: dict,
    upload_b64: str | None = None,
    user_id: str | None = None,
) -> int:
    """Add job to queue. Returns queue row id."""
    from prismrag.db import get_conn, release_conn

    payload = {
        "request": request_dict,
        "upload_b64": upload_b64,
        "user_id": user_id,
    }
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO prismrag.job_queue (job_id, tenant_id, payload, status)
            VALUES (%s, %s, %s::jsonb, 'pending')
            RETURNING id
            """,
            (job_id, tenant_id, json.dumps(payload)),
        )
        qid = cur.fetchone()[0]
        conn.commit()
        return qid
    finally:
        release_conn(conn)


def _claim_next() -> dict | None:
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE prismrag.job_queue
            SET status = 'running', worker_id = %s, claimed_at = now(), attempts = attempts + 1
            WHERE id = (
                SELECT id FROM prismrag.job_queue
                WHERE status = 'pending' AND attempts < max_attempts
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, job_id::text, tenant_id::text, payload, attempts
            """,
            (WORKER_ID,),
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        return {
            "queue_id": row[0],
            "job_id": row[1],
            "tenant_id": row[2],
            "payload": row[3] if isinstance(row[3], dict) else json.loads(row[3]),
            "attempts": row[4],
        }
    finally:
        release_conn(conn)


def _finish(queue_id: int, status: str, error: str | None = None) -> None:
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE prismrag.job_queue
            SET status = %s, finished_at = now(), error_message = %s
            WHERE id = %s
            """,
            (status, error, queue_id),
        )
        conn.commit()
    finally:
        release_conn(conn)


def process_one() -> bool:
    """Claim and run one queued job. Returns True if work was done."""
    item = _claim_next()
    if not item:
        return False

    queue_id = item["queue_id"]
    job_id = item["job_id"]
    payload = item["payload"]

    try:
        import base64
        from prismrag.models import JobRequest
        from prismrag.pipeline.job import run_job, get_job
        from prismrag.metering.quota import check_and_record

        request = JobRequest.model_validate(payload["request"])
        upload_bytes = None
        if payload.get("upload_b64"):
            upload_bytes = base64.b64decode(payload["upload_b64"])

        user_id = payload.get("user_id")
        plan = "free"
        if user_id:
            from prismrag.auth.auth import _load_user
            user = _load_user(user_id)
            plan = user.get("plan", "free")

        run_job(
            job_id, request, upload_bytes,
            user_id=user_id, plan=plan,
        )

        if user_id:
            from prismrag.auth.auth import _load_user
            user = _load_user(user_id)
            job = get_job(job_id)
            chunks = job.get("recordsWritten", 0) if job else 0
            if chunks > 0:
                check_and_record(user, "ingest_chunk", chunks, str(request.tenant_id))

        from prismrag.middleware.metrics import record_job_completion
        record_job_completion("completed")
        _finish(queue_id, "done")
    except Exception as exc:
        logger.exception("Worker failed job %s", job_id)
        from prismrag.middleware.metrics import record_job_completion
        record_job_completion("failed")
        if item["attempts"] >= 3:
            _finish(queue_id, "failed", str(exc)[:500])
        else:
            from prismrag.db import get_conn, release_conn
            conn = get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE prismrag.job_queue SET status = 'pending', worker_id = NULL WHERE id = %s",
                    (queue_id,),
                )
                conn.commit()
            finally:
                release_conn(conn)
    return True


def run_forever() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Job worker %s started (poll=%ss)", WORKER_ID, POLL_INTERVAL)
    while True:
        if not process_one():
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    from prismrag.db import init_schema
    init_schema()
    run_forever()
