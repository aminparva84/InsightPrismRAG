"""PrismRAG — Search and ingest result audit logging."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from prismrag.plans import get_plan_limits


def _retention_days(plan: str) -> int:
    return int(get_plan_limits(plan).get("log_retention_days", 30))


def log_search_result(
    *,
    user_id: str | None,
    tenant_id: str,
    mapping_id: str | None,
    query_text: str,
    query_embedding: list[float] | None,
    top_k: int,
    category_filter: str | None,
    results: dict[str, Any],
    retrieval_mode: str,
    latency_ms: int | None,
    plan: str = "free",
) -> None:
    def _write():
        from prismrag.db import get_conn, release_conn, vector_to_pg

        expires = datetime.now(timezone.utc) + timedelta(days=_retention_days(plan))
        conn = get_conn()
        try:
            cur = conn.cursor()
            sem_pg = vector_to_pg(query_embedding) if query_embedding else None
            cur.execute(
                """
                INSERT INTO prismrag.search_result_log
                    (user_id, tenant_id, mapping_id, query_text, query_embedding,
                     top_k, category_filter, results, retrieval_mode, latency_ms, expires_at)
                VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s::jsonb, %s, %s, %s)
                """,
                (
                    user_id, tenant_id, mapping_id, query_text, sem_pg,
                    top_k, category_filter, json.dumps(results),
                    retrieval_mode, latency_ms, expires,
                ),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            release_conn(conn)

    threading.Thread(target=_write, daemon=True).start()


def log_ingest_result(
    *,
    job_id: str,
    user_id: str | None,
    tenant_id: str,
    mapping_id: str | None,
    strategy: str | None,
    records_total: int | None,
    records_written: int,
    records_failed: int = 0,
    mlp_val_recall: float | None = None,
    community_count: int | None = None,
    duration_s: int | None = None,
    error_summary: str | None = None,
    plan: str = "free",
) -> None:
    def _write():
        from prismrag.db import get_conn, release_conn

        expires = datetime.now(timezone.utc) + timedelta(days=_retention_days(plan))
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO prismrag.ingest_result_log
                    (job_id, user_id, tenant_id, mapping_id, strategy,
                     records_total, records_written, records_failed,
                     mlp_val_recall, community_count, duration_s, error_summary, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    job_id, user_id, tenant_id, mapping_id, strategy,
                    records_total, records_written, records_failed,
                    mlp_val_recall, community_count, duration_s,
                    (error_summary or "")[:2000] or None, expires,
                ),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            release_conn(conn)

    threading.Thread(target=_write, daemon=True).start()
