"""
PrismRAG — Result quality logging.

Records quality signals for every search and deliberation response so you can
evaluate retrieval accuracy and synthesis quality over time in production.

Writes to: prismrag.quality_log (created by schema below)
Access via: GET /api/v1/prismrag/quality/summary
            GET /api/v1/deliberation/quality/summary
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── DB helpers ────────────────────────────────────────────────────────────────

def _write_bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def _conn():
    from prismrag.db import get_conn
    return get_conn()

def _rel(conn):
    from prismrag.db import release_conn
    release_conn(conn)


# ── Search quality log ────────────────────────────────────────────────────────

def log_search(
    *,
    tenant_id: str,
    query: str,
    top_k: int,
    results: list[dict],
    latency_ms: int,
    user_id: Optional[str] = None,
    mapping_id: Optional[str] = None,
    category_filter: Optional[str] = None,
) -> None:
    """
    Log a search event for quality analysis.

    Computes and stores:
      - result_count: how many results returned
      - top_category: category_slug of #1 result
      - score_spread: max_score - min_score (discrimination power)
      - mean_score: average score across results
      - has_category_filter: was a filter applied?
    """
    if not results:
        top_category = None
        score_spread = 0.0
        mean_score   = 0.0
    else:
        scores       = [r.get("score", 0.0) for r in results]
        top_category = results[0].get("category_slug")
        score_spread = max(scores) - min(scores) if len(scores) > 1 else 0.0
        mean_score   = sum(scores) / len(scores)

    payload = {
        "tenant_id":          tenant_id,
        "user_id":            user_id,
        "mapping_id":         mapping_id,
        "query":              query[:500],
        "top_k":              top_k,
        "result_count":       len(results),
        "top_category":       top_category,
        "category_filter":    category_filter,
        "score_spread":       round(score_spread, 4),
        "mean_score":         round(mean_score, 4),
        "latency_ms":         latency_ms,
        "results_sample":     results[:3],  # store top 3 for inspection
    }
    _write_bg(_persist_search_quality, payload)


def _persist_search_quality(payload: dict) -> None:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO prismrag.quality_search_log
              (tenant_id, user_id, mapping_id, query, top_k, result_count,
               top_category, category_filter, score_spread, mean_score,
               latency_ms, results_sample)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            """,
            (
                payload["tenant_id"], payload["user_id"], payload["mapping_id"],
                payload["query"], payload["top_k"], payload["result_count"],
                payload["top_category"], payload["category_filter"],
                payload["score_spread"], payload["mean_score"],
                payload["latency_ms"],
                json.dumps(payload["results_sample"]),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("quality_search_log write failed: %s", exc)
    finally:
        _rel(conn)


# ── Deliberation quality log ─────────────────────────────────────────────────

def log_deliberation(
    *,
    session_id: str,
    user_id: Optional[str],
    tenant_id: Optional[str],
    question: str,
    domain_count: int,
    domains_discovered: list[dict],
    verticals: list[dict],
    synthesis: Optional[dict],
    total_latency_ms: int,
    phase_latencies_ms: Optional[dict] = None,
) -> None:
    """
    Log a deliberation result for quality analysis.

    Computes and stores:
      - domains_discovered: how many domains were found
      - vertical_mean_confidence: average confidence across vertical expert calls
      - synthesis_confidence: master deliberator confidence
      - completeness_score: fraction of synthesis fields non-empty
      - conflict_detected: whether conflicts field is substantive
      - total_latency_ms
    """
    if not synthesis:
        completeness = 0.0
        synth_conf   = 0.0
        conflict_det = False
    else:
        fields = [
            synthesis.get("agreements", ""),
            synthesis.get("conflicts", ""),
            synthesis.get("unique_insights", ""),
            synthesis.get("final_answer", ""),
        ]
        completeness = sum(1 for f in fields if f and len(f) > 20) / len(fields)
        synth_conf   = synthesis.get("confidence", 0.0) or 0.0
        conflict_det = len(synthesis.get("conflicts", "")) > 30

    vert_confs = [v.get("confidence", 0.0) for v in verticals if v.get("confidence") is not None]
    vert_mean_conf = sum(vert_confs) / len(vert_confs) if vert_confs else 0.0

    payload = {
        "session_id":              session_id,
        "user_id":                 user_id,
        "tenant_id":               tenant_id,
        "question":                question[:500],
        "domain_count_requested":  domain_count,
        "domains_discovered":      len(domains_discovered),
        "domain_sources":          json.dumps([d.get("source") for d in domains_discovered]),
        "vertical_mean_confidence": round(vert_mean_conf, 3),
        "synthesis_confidence":    round(synth_conf, 3),
        "completeness_score":      round(completeness, 3),
        "conflict_detected":       conflict_det,
        "total_latency_ms":        total_latency_ms,
        "phase_latencies_ms":      json.dumps(phase_latencies_ms or {}),
    }
    _write_bg(_persist_deliberation_quality, payload)


def _persist_deliberation_quality(payload: dict) -> None:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO prismrag.quality_deliberation_log
              (session_id, user_id, tenant_id, question, domain_count_requested,
               domains_discovered, domain_sources, vertical_mean_confidence,
               synthesis_confidence, completeness_score, conflict_detected,
               total_latency_ms, phase_latencies_ms)
            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)
            """,
            (
                payload["session_id"], payload["user_id"], payload["tenant_id"],
                payload["question"], payload["domain_count_requested"],
                payload["domains_discovered"], payload["domain_sources"],
                payload["vertical_mean_confidence"], payload["synthesis_confidence"],
                payload["completeness_score"], payload["conflict_detected"],
                payload["total_latency_ms"], payload["phase_latencies_ms"],
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("quality_deliberation_log write failed: %s", exc)
    finally:
        _rel(conn)


# ── Summary queries ───────────────────────────────────────────────────────────

def search_quality_summary(tenant_id: str, days: int = 7) -> dict:
    """Return aggregated search quality metrics for a tenant over the last N days."""
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              COUNT(*)                          AS total_searches,
              AVG(score_spread)                 AS avg_score_spread,
              AVG(mean_score)                   AS avg_mean_score,
              AVG(result_count)                 AS avg_result_count,
              PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
              PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
              COUNT(*) FILTER (WHERE result_count = 0) AS zero_result_count,
              MODE() WITHIN GROUP (ORDER BY top_category) AS top_category_mode
            FROM prismrag.quality_search_log
            WHERE tenant_id = %s
              AND created_at >= now() - (%s || ' days')::interval
            """,
            (tenant_id, str(days)),
        )
        row = cur.fetchone()
        if not row or row[0] == 0:
            return {"tenant_id": tenant_id, "period_days": days, "total_searches": 0}
        return {
            "tenant_id":          tenant_id,
            "period_days":        days,
            "total_searches":     row[0],
            "avg_score_spread":   round(float(row[1] or 0), 4),
            "avg_mean_score":     round(float(row[2] or 0), 4),
            "avg_result_count":   round(float(row[3] or 0), 1),
            "p50_latency_ms":     int(row[4] or 0),
            "p95_latency_ms":     int(row[5] or 0),
            "zero_result_rate":   round(row[6] / row[0], 3),
            "top_category_mode":  row[7],
        }
    finally:
        _rel(conn)


def deliberation_quality_summary(user_id: Optional[str] = None, days: int = 7) -> dict:
    """Return aggregated deliberation quality metrics."""
    conn = _conn()
    try:
        cur = conn.cursor()
        where = "created_at >= now() - (%s || ' days')::interval"
        params: list[Any] = [str(days)]
        if user_id:
            where += " AND user_id = %s"
            params.append(user_id)
        cur.execute(
            f"""
            SELECT
              COUNT(*)                           AS total,
              AVG(domains_discovered)            AS avg_domains,
              AVG(vertical_mean_confidence)      AS avg_vert_conf,
              AVG(synthesis_confidence)          AS avg_synth_conf,
              AVG(completeness_score)            AS avg_completeness,
              COUNT(*) FILTER (WHERE conflict_detected) AS conflicts_found,
              PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_latency_ms) AS p50_ms,
              PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_latency_ms) AS p95_ms
            FROM prismrag.quality_deliberation_log
            WHERE {where}
            """,
            params,
        )
        row = cur.fetchone()
        if not row or row[0] == 0:
            return {"period_days": days, "total_deliberations": 0}
        return {
            "period_days":           days,
            "total_deliberations":   row[0],
            "avg_domains_discovered":round(float(row[1] or 0), 1),
            "avg_vertical_conf":     round(float(row[2] or 0), 3),
            "avg_synthesis_conf":    round(float(row[3] or 0), 3),
            "avg_completeness":      round(float(row[4] or 0), 3),
            "conflict_detection_rate":round(row[5] / row[0], 3),
            "p50_latency_ms":        int(row[6] or 0),
            "p95_latency_ms":        int(row[7] or 0),
        }
    finally:
        _rel(conn)
