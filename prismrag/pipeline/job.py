"""PrismRAG — Job runner: source → mapping → vector writer → graph."""
from __future__ import annotations

import logging
import traceback
import uuid
from datetime import datetime, timezone

from prismrag.config import SYNC_MAX_RECORDS, INGEST_BATCH_SIZE
from prismrag.db import get_conn, release_conn
from prismrag.models import (
    JobRequest, JobStatus, StrategyType, SourceType,
    MappingConfigIn, CategoryIn, MappingRuleIn,
)

logger = logging.getLogger(__name__)


# ── Job DB helpers ────────────────────────────────────────────────────────────

def create_job(tenant_id: str, request: JobRequest) -> str:
    job_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        cur = conn.cursor()
        import json
        cur.execute(
            """
            INSERT INTO prismrag.ingest_job
                (id, tenant_id, source_type, source_config, status, timeout_at, created_at)
            VALUES (%s, %s, %s, %s::jsonb, 'queued',
                    now() + interval '%s seconds', now())
            """,
            (job_id, tenant_id, request.source_type.value,
             json.dumps(request.dict(exclude={"mapping"})),
             3600 * 2),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return job_id


def _update_job(job_id: str, **fields) -> None:
    allowed = {
        "status", "records_total", "records_written",
        "progress_pct", "error_message", "started_at", "finished_at", "mapping_id",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = %s")
            vals.append(v)
    if not sets:
        return
    vals.append(job_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE prismrag.ingest_job SET {', '.join(sets)} WHERE id = %s", vals)
        conn.commit()
    finally:
        release_conn(conn)


def get_job(job_id: str) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tenant_id, status, records_total, records_written,
                   progress_pct, error_message, started_at, finished_at, timeout_at
            FROM prismrag.ingest_job WHERE id = %s
            """,
            (job_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        status = row[2]
        timeout_at = row[9]
        if status in ("queued", "running") and timeout_at:
            now = datetime.now(timezone.utc)
            ta  = timeout_at if hasattr(timeout_at, "tzinfo") else timeout_at.replace(tzinfo=timezone.utc)
            if now > ta:
                status = "stale"
        return {
            "jobId":         str(row[0]),
            "tenantId":      str(row[1]),
            "status":        status,
            "recordsTotal":  row[3],
            "recordsWritten": row[4],
            "progressPct":   row[5],
            "errorMessage":  row[6],
            "startedAt":     row[7].isoformat() if row[7] else None,
            "finishedAt":    row[8].isoformat() if row[8] else None,
        }
    finally:
        release_conn(conn)


# ── Mapping persistence ───────────────────────────────────────────────────────

def _persist_mapping(tenant_id: str, mapping_config: MappingConfigIn, strategy: str) -> str:
    """Insert mapping_version + categories + rules. Returns mapping_id."""
    mapping_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        cur = conn.cursor()
        # Deactivate previous active mapping for this tenant
        cur.execute(
            "UPDATE prismrag.mapping_version SET status='archived' "
            "WHERE tenant_id = %s AND status = 'active'",
            (tenant_id,),
        )
        import json
        cur.execute(
            """
            INSERT INTO prismrag.mapping_version (id, tenant_id, strategy, status, config_json)
            VALUES (%s, %s, %s, 'active', %s::jsonb)
            """,
            (mapping_id, tenant_id, strategy, json.dumps({})),
        )
        for cat in mapping_config.categories:
            cur.execute(
                "INSERT INTO prismrag.mapping_category (mapping_id, category_slug, category_label, sort_order) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (mapping_id, cat.slug, cat.label, cat.sort_order),
            )
        for rule in mapping_config.rules:
            cur.execute(
                "INSERT INTO prismrag.mapping_rule (mapping_id, word, category_slug, weight, source) "
                "VALUES (%s, %s, %s, %s, 'intake') ON CONFLICT DO NOTHING",
                (mapping_id, rule.word.strip().lower(), rule.category_slug, rule.weight),
            )
        conn.commit()
        return mapping_id
    finally:
        release_conn(conn)


# ── Strategy factory ──────────────────────────────────────────────────────────

def _build_strategy(request: JobRequest, mapping_id: str, word_texts: dict[str, str]):
    if request.strategy == StrategyType.rules:
        from prismrag.mapping.rules import RulesStrategy
        return RulesStrategy(request.mapping)

    if request.strategy == StrategyType.mlp:
        from prismrag.mapping.mlp import MLPStrategy
        strat = MLPStrategy(
            request.mapping,
            word_texts=word_texts,
            epochs=request.mlp_epochs or 180,
            recall_target=request.mlp_recall_target or 0.85,
        )
        recall = strat.train()
        logger.info("MLP trained (val_recall=%.3f) for mapping %s", recall, mapping_id[:8])
        # Persist MLP weights
        if strat.weights_blob:
            _save_mlp_artifact(request.tenant_id, mapping_id, strat)
        return strat

    if request.strategy == StrategyType.cluster:
        from prismrag.mapping.rules import RulesStrategy
        logger.warning("ClusterStrategy not yet implemented — falling back to RulesStrategy")
        return RulesStrategy(request.mapping)

    raise NotImplementedError(f"Strategy {request.strategy} not implemented yet")


def _save_mlp_artifact(tenant_id, mapping_id: str, strat) -> None:
    from psycopg2 import Binary
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO prismrag.mlp_artifact
                (tenant_id, mapping_id, weights_blob, embed_dim, recall_at_10)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, mapping_id) DO UPDATE
            SET weights_blob = EXCLUDED.weights_blob,
                recall_at_10 = EXCLUDED.recall_at_10,
                trained_at   = now()
            """,
            (str(tenant_id), mapping_id, Binary(strat.weights_blob),
             256, strat.val_recall),
        )
        conn.commit()
    finally:
        release_conn(conn)


# ── Source adapter factory ────────────────────────────────────────────────────

def _build_adapter(request: JobRequest, upload_bytes: bytes | None = None):
    if request.source_type == SourceType.file:
        from prismrag.adapters.file import FileAdapter
        if upload_bytes is None:
            raise ValueError("upload_bytes required for file source")
        return FileAdapter(upload_bytes, request.file_config)

    if request.source_type == SourceType.sql:
        from prismrag.adapters.sql import SQLAdapter
        return SQLAdapter(request.sql_config)

    if request.source_type == SourceType.api:
        from prismrag.adapters.api import APIAdapter
        return APIAdapter(request.api_config)

    if request.source_type == SourceType.chunk:
        from prismrag.adapters.chunk import ChunkAdapter
        return ChunkAdapter(request.chunk_config)

    if request.source_type == SourceType.inline:
        from prismrag.adapters.inline import InlineAdapter
        return InlineAdapter(request.inline_config)

    raise NotImplementedError(f"Source type {request.source_type} not implemented")


# ── Core pipeline ─────────────────────────────────────────────────────────────

def run_job(
    job_id: str,
    request: JobRequest,
    upload_bytes: bytes | None = None,
    *,
    user_id: str | None = None,
    plan: str = "free",
) -> dict:
    """
    Execute the full ingest pipeline for one job.

    Designed to run in a background thread or Celery worker.
    Updates job progress in DB as it proceeds.
    """
    _update_job(job_id, status="running", started_at=datetime.now(timezone.utc))
    started = datetime.now(timezone.utc)
    mapping_id_result: str | None = None
    community_count = 0

    try:
        # 1. Persist mapping definition (Tier-1 rules)
        mapping_id = _persist_mapping(
            str(request.tenant_id), request.mapping, request.strategy.value
        )
        mapping_id_result = mapping_id
        _update_job(job_id, mapping_id=mapping_id)

        # 2. Build source adapter
        adapter = _build_adapter(request, upload_bytes)

        # 3. Stream all records to collect word→text for MLP training
        #    (we need word_texts before training; for huge sources we sample)
        records_buf: list = []
        word_texts: dict[str, str] = {}
        total_estimate = adapter.count_estimate()

        for rec in adapter.stream():
            records_buf.append(rec)
            word_texts[rec.word] = rec.text
            if total_estimate and len(records_buf) % 1000 == 0:
                pct = min(10, int(10 * len(records_buf) / total_estimate))
                _update_job(job_id, records_total=total_estimate, progress_pct=pct)

        total = len(records_buf)
        _update_job(job_id, records_total=total, progress_pct=15)

        # 4. Build mapping strategy (trains MLP if Tier-2)
        strategy = _build_strategy(request, mapping_id, word_texts)
        _update_job(job_id, progress_pct=40)

        # 5. Write vectors in batches
        written = _write_batches(
            job_id, str(request.tenant_id), mapping_id, strategy, records_buf
        )

        # 6. Build word graph + communities
        _update_job(job_id, progress_pct=90, records_written=written)
        try:
            _build_graph_and_communities(str(request.tenant_id), mapping_id)
            community_count = _count_communities(str(request.tenant_id), mapping_id)
        except Exception as exc:
            logger.warning("Graph build non-fatal error: %s", exc)

        finished = datetime.now(timezone.utc)
        _update_job(
            job_id,
            status="completed",
            records_written=written,
            progress_pct=100,
            finished_at=finished,
        )

        from prismrag.audit.results import log_ingest_result
        log_ingest_result(
            job_id=job_id,
            user_id=user_id,
            tenant_id=str(request.tenant_id),
            mapping_id=mapping_id_result,
            strategy=request.strategy.value,
            records_total=total,
            records_written=written,
            community_count=community_count,
            duration_s=int((finished - started).total_seconds()),
            plan=plan,
        )

        from prismrag.middleware.metrics import record_job_completion
        record_job_completion("completed")

        # 7. Webhook callback
        if request.webhook_url:
            _fire_webhook(request.webhook_url, job_id, "completed")

        return {"jobId": job_id, "status": "completed",
                "recordsTotal": total, "recordsWritten": written}

    except Exception as exc:
        logger.error("Job %s failed: %s", job_id, exc)
        traceback.print_exc()
        finished = datetime.now(timezone.utc)
        _update_job(
            job_id,
            status="failed",
            error_message=str(exc)[:500],
            finished_at=finished,
        )
        from prismrag.audit.results import log_ingest_result
        log_ingest_result(
            job_id=job_id,
            user_id=user_id,
            tenant_id=str(request.tenant_id),
            mapping_id=mapping_id_result,
            strategy=request.strategy.value,
            records_total=None,
            records_written=0,
            error_summary=str(exc),
            duration_s=int((finished - started).total_seconds()),
            plan=plan,
        )
        from prismrag.middleware.metrics import record_job_completion
        record_job_completion("failed")
        if request.webhook_url:
            _fire_webhook(request.webhook_url, job_id, "failed")
        raise


def _write_batches(
    job_id: str,
    tenant_id: str,
    mapping_id: str,
    strategy,
    records: list,
) -> int:
    from prismrag.db import get_conn, release_conn, vector_to_pg
    import json

    conn = get_conn()
    written = 0
    total = len(records)

    try:
        cur = conn.cursor()
        for batch_start in range(0, total, INGEST_BATCH_SIZE):
            batch = records[batch_start: batch_start + INGEST_BATCH_SIZE]
            inputs = [(r.word, r.text, r.category_hint) for r in batch]
            results = strategy.assign_batch(inputs)

            for rec, res in zip(batch, results):
                sem_pg = vector_to_pg(res.sem_embedding.tolist()) if res.sem_embedding is not None else None
                cur.execute(
                    """
                    INSERT INTO prismrag.chunk_embedding
                        (tenant_id, mapping_id, chunk_text, chunk_ref, category_slug,
                         embedding, sem_embedding, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s::vector, %s::vector, %s::jsonb)
                    ON CONFLICT (tenant_id, mapping_id, chunk_ref) DO UPDATE SET
                        chunk_text    = EXCLUDED.chunk_text,
                        category_slug = EXCLUDED.category_slug,
                        embedding     = EXCLUDED.embedding,
                        sem_embedding = EXCLUDED.sem_embedding
                    """,
                    (
                        tenant_id, mapping_id, rec.text,
                        rec.ref or f"{rec.word}:{batch_start}",
                        res.category_slug,
                        vector_to_pg(res.embedding.tolist()),
                        sem_pg,
                        json.dumps(rec.metadata),
                    ),
                )
                written += 1

            conn.commit()
            pct = 40 + int(50 * written / max(total, 1))
            _update_job(job_id, records_written=written, progress_pct=pct)
    finally:
        release_conn(conn)

    return written


def _build_graph_and_communities(tenant_id: str, mapping_id: str) -> None:
    from prismrag.graph.builder import build_graph
    from prismrag.graph.community import build_communities
    build_graph(tenant_id, mapping_id)
    build_communities(tenant_id, mapping_id)


def _count_communities(tenant_id: str, mapping_id: str) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM prismrag.community_summary WHERE tenant_id = %s AND mapping_id = %s",
            (tenant_id, mapping_id),
        )
        return int(cur.fetchone()[0])
    finally:
        release_conn(conn)


def _fire_webhook(url: str, job_id: str, status: str) -> None:
    try:
        import requests
        requests.post(url, json={"jobId": job_id, "status": status}, timeout=10)
    except Exception as exc:
        logger.warning("Webhook %s failed: %s", url, exc)
