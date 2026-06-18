"""
PrismRAG — Chunk quality scoring.

Three complementary metrics combined into one quality_score (0-1):

  confidence   How decisively the category was assigned.
               • Tier 1 (rules): fraction of rule-weight in the winning category
                 vs total matched rule-weight.
               • Tier 2 (MLP): softmax probability of the winning centroid.

  separation   Distance gap between the assigned centroid and the nearest
               rival centroid.  High → chunk sits deep inside its cluster.
               Formula: clip((sim_best - sim_second + 1) / 2, 0, 1)

  coherence    Average cosine similarity to the nearest 5 peers in the same
               category.  High → chunk is typical of its category.

Combined:  quality_score = 0.40*confidence + 0.40*separation + 0.20*coherence

quality_score < LOW_QUALITY_THRESHOLD → flagged for ML fallback review.
"""
from __future__ import annotations

import logging
from typing import NamedTuple

import numpy as np

logger = logging.getLogger(__name__)

LOW_QUALITY_THRESHOLD = 0.45   # chunks below this get flagged


class ChunkQuality(NamedTuple):
    chunk_ref:     str
    confidence:    float   # 0-1
    separation:    float   # 0-1
    coherence:     float   # 0-1
    quality_score: float   # combined 0-1
    flagged:       bool    # True if below LOW_QUALITY_THRESHOLD


def score_batch(
    chunk_refs:     list[str],
    embeddings:     np.ndarray,          # (N, D) personal embeddings, unit or non-unit
    category_slugs: list[str],
    confidences:    list[float] | None = None,   # pre-computed; None = derive from centroids
) -> list[ChunkQuality]:
    """
    Score quality for a batch of N chunks.

    chunk_refs      unique reference string per chunk
    embeddings      (N, D) personal embeddings (Tier-1 256-d or MLP output)
    category_slugs  assigned category slug per chunk
    confidences     optional pre-computed confidence values (e.g. MLP softmax);
                    if None, confidence is derived from cosine sim to category centroid
    """
    n = len(chunk_refs)
    if n == 0:
        return []

    embs = np.asarray(embeddings, dtype=float)
    norms = np.linalg.norm(embs, axis=1, keepdims=True).clip(min=1e-8)
    embs_n = embs / norms   # unit vectors (N, D)

    # Build per-category centroids (unit-normalised)
    cats_unique = list(dict.fromkeys(category_slugs))  # preserve insertion order, dedup
    centroids: dict[str, np.ndarray] = {}
    for c in cats_unique:
        mask = [i for i, s in enumerate(category_slugs) if s == c]
        cent = embs_n[mask].mean(axis=0)
        norm_c = np.linalg.norm(cent)
        centroids[c] = cent / norm_c if norm_c > 1e-8 else cent

    scores: list[ChunkQuality] = []
    for i in range(n):
        vec      = embs_n[i]
        assigned = category_slugs[i]

        # ── Confidence ────────────────────────────────────────────────────────
        if confidences is not None:
            conf = float(np.clip(confidences[i], 0.0, 1.0))
        else:
            cent = centroids.get(assigned, np.zeros_like(vec))
            conf = float(np.clip(vec @ cent, 0.0, 1.0))

        # ── Separation ────────────────────────────────────────────────────────
        sims = {c: float(vec @ centroids[c]) for c in cats_unique}
        sim_best = sims[assigned]
        others   = [v for c, v in sims.items() if c != assigned]
        if others:
            sim_second = max(others)
            separation = float(np.clip((sim_best - sim_second + 1.0) / 2.0, 0.0, 1.0))
        else:
            separation = 1.0   # sole category

        # ── Coherence ─────────────────────────────────────────────────────────
        peers = [j for j, s in enumerate(category_slugs) if s == assigned and j != i]
        if peers:
            peer_sims = embs_n[peers] @ vec   # (P,)
            top_k     = min(5, len(peers))
            coherence = float(np.clip(np.partition(peer_sims, -top_k)[-top_k:].mean(), 0.0, 1.0))
        else:
            coherence = 1.0   # sole member

        quality = 0.40 * conf + 0.40 * separation + 0.20 * coherence
        scores.append(ChunkQuality(
            chunk_ref=chunk_refs[i],
            confidence=round(conf, 4),
            separation=round(separation, 4),
            coherence=round(coherence, 4),
            quality_score=round(quality, 4),
            flagged=quality < LOW_QUALITY_THRESHOLD,
        ))

    return scores


def score_mapping_from_db(tenant_id: str, mapping_id: str) -> list[dict]:
    """
    Load all chunks for a mapping from DB and return quality scores.
    Used by GET /tenants/{id}/chunks/quality.
    """
    import json
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT chunk_ref, category_slug, embedding::text
            FROM prismrag.chunk_embedding
            WHERE tenant_id = %s AND mapping_id = %s
            ORDER BY chunk_ref
            """,
            (tenant_id, mapping_id),
        )
        rows = cur.fetchall()
    finally:
        release_conn(conn)

    if not rows:
        return []

    refs = [r[0] for r in rows]
    cats = [r[1] for r in rows]
    embs = np.array(
        [json.loads(r[2]) if r[2] else [0.0] * 256 for r in rows],
        dtype=float,
    )

    return [q._asdict() for q in score_batch(refs, embs, cats)]


def summarise_quality(scores: list[dict]) -> dict:
    """Aggregate quality scores into a summary dict."""
    if not scores:
        return {"total": 0, "flagged": 0, "avg_quality": None}

    qs = [s["quality_score"] for s in scores]
    return {
        "total":          len(scores),
        "flagged":        sum(1 for s in scores if s["flagged"]),
        "pct_flagged":    round(100 * sum(1 for s in scores if s["flagged"]) / len(scores), 1),
        "avg_quality":    round(float(np.mean(qs)), 4),
        "avg_confidence": round(float(np.mean([s["confidence"] for s in scores])), 4),
        "avg_separation": round(float(np.mean([s["separation"] for s in scores])), 4),
        "avg_coherence":  round(float(np.mean([s["coherence"]  for s in scores])), 4),
        "min_quality":    round(float(np.min(qs)), 4),
        "p25_quality":    round(float(np.percentile(qs, 25)), 4),
        "p50_quality":    round(float(np.percentile(qs, 50)), 4),
        "p75_quality":    round(float(np.percentile(qs, 75)), 4),
    }
