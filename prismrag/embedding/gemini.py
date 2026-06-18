"""Gemini embedding — cached in prismrag.semantic_embedding."""
from __future__ import annotations

import logging
import os
from typing import Sequence

import numpy as np
import requests

from prismrag.config import (
    GEMINI_API_KEY,
    GEMINI_EMBED_MODEL,
    EMBED_BATCH_SIZE,
    EMBED_DIM_SEMANTIC,
)

logger = logging.getLogger(__name__)


def _embed_url() -> str:
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_EMBED_MODEL}:batchEmbedContents"
    )


def _embed_request(text: str) -> dict:
    """Build one batchEmbedContents request item."""
    req: dict = {
        "model": f"models/{GEMINI_EMBED_MODEL}",
        "content": {"parts": [{"text": text}]},
    }
    # gemini-embedding-* defaults to 3072-d; schema expects 768-d vectors
    if GEMINI_EMBED_MODEL.startswith("gemini-embedding"):
        req["outputDimensionality"] = EMBED_DIM_SEMANTIC
    return req


def _call_gemini_batch(texts: list[str]) -> list[list[float] | None]:
    """Call Gemini batchEmbedContents. Returns one vector per text (or None on error)."""
    api_key = (GEMINI_API_KEY or os.getenv("GEMINI_API_KEY") or "").strip().lstrip("\ufeff")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — returning zero vectors")
        return [None] * len(texts)

    url = f"{_embed_url()}?key={api_key}"
    body = {"requests": [_embed_request(t) for t in texts]}
    try:
        resp = requests.post(url, json=body, timeout=30)
        resp.raise_for_status()
        embeddings = resp.json().get("embeddings", [])
        return [e.get("values") for e in embeddings]
    except Exception as exc:
        logger.error("Gemini batch embed failed: %s", exc)
        return [None] * len(texts)


def embed_texts(texts: Sequence[str]) -> list[list[float] | None]:
    """
    Return 768-d Gemini vectors for each text.
    Uses prismrag.semantic_embedding as a shared cache.
    Falls back to direct API if DB unavailable.
    """
    texts = list(texts)
    if not texts:
        return []

    result: list[list[float] | None] = [None] * len(texts)
    idx_map: dict[str, list[int]] = {}  # word → positions needing lookup

    for i, t in enumerate(texts):
        idx_map.setdefault(t, []).append(i)

    # Check DB cache
    cached: dict[str, list[float]] = {}
    try:
        from prismrag.db import get_conn, release_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            unique_texts = list(idx_map.keys())
            cur.execute(
                "SELECT word, vec::text FROM prismrag.semantic_embedding WHERE word = ANY(%s) AND model = %s",
                (unique_texts, GEMINI_EMBED_MODEL),
            )
            import json
            for word, vec_str in cur.fetchall():
                cached[word] = json.loads(vec_str) if isinstance(vec_str, str) else list(vec_str)
        finally:
            release_conn(conn)
    except Exception as exc:
        logger.debug("Cache lookup skipped: %s", exc)

    # Fill cached hits
    for word, positions in idx_map.items():
        if word in cached:
            for pos in positions:
                result[pos] = cached[word]

    # Batch-embed misses
    missing_words = [w for w in idx_map if w not in cached]
    if missing_words:
        for batch_start in range(0, len(missing_words), EMBED_BATCH_SIZE):
            batch = missing_words[batch_start: batch_start + EMBED_BATCH_SIZE]
            vecs = _call_gemini_batch(batch)
            _store_cache(list(zip(batch, vecs)))
            for word, vec in zip(batch, vecs):
                if vec is not None:
                    for pos in idx_map[word]:
                        result[pos] = vec

    return result


def _store_cache(pairs: list[tuple[str, list[float] | None]]) -> None:
    valid = [(w, v) for w, v in pairs if v is not None]
    if not valid:
        return
    try:
        from prismrag.db import get_conn, release_conn, vector_to_pg
        conn = get_conn()
        try:
            cur = conn.cursor()
            for word, vec in valid:
                cur.execute(
                    """
                    INSERT INTO prismrag.semantic_embedding (word, model, vec)
                    VALUES (%s, %s, %s::vector)
                    ON CONFLICT (word, model) DO NOTHING
                    """,
                    (word, GEMINI_EMBED_MODEL, vector_to_pg(vec)),
                )
            conn.commit()
        finally:
            release_conn(conn)
    except Exception as exc:
        logger.debug("Cache write skipped: %s", exc)


def mean_embed(texts: Sequence[str]) -> np.ndarray | None:
    """Embed texts and return the mean vector (normalised). Returns None if all fail."""
    vecs = embed_texts(texts)
    valid = [np.array(v, dtype=float) for v in vecs if v is not None]
    if not valid:
        return None
    arr = np.mean(valid, axis=0)
    norm = np.linalg.norm(arr)
    return (arr / norm) if norm > 0 else arr
