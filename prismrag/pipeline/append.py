"""
PrismRAG — Tier-2 Append mode.

Clients POST new raw chunks + optional new mapping rules to extend an existing
MLP-backed mapping.  No full retrain.  The existing MLP generalises to the
new data; if new_rules are provided we fine-tune only the final projection
layer (freeze-then-finetune to avoid catastrophic forgetting).

Contract:
  • Existing chunks are NOT touched.
  • New chunks are UPSERTED into prismrag.chunk_embedding on (tenant_id, mapping_id, chunk_ref).
  • If chunk_ref already exists the text, category, and embedding are updated in-place.
  • Every chunk gets a quality_score.  Flagged chunks (score < threshold) are still written
    but are surfaced so the caller can inspect or re-process them.
  • ml_fallback controls what happens when quality is low:
      "auto"   (default) — for flagged chunks, also run zero-shot rule lookup;
                            keep whichever assignment has higher confidence.
      "always" — run rules lookup for every chunk regardless of quality.
      "never"  — pure MLP, no fallback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from prismrag.db import get_conn, release_conn
from prismrag.pipeline.quality import score_batch, LOW_QUALITY_THRESHOLD

logger = logging.getLogger(__name__)


# ── Public request / result types ─────────────────────────────────────────────

@dataclass
class ChunkIn:
    ref:  str
    text: str


@dataclass
class RuleIn:
    word:          str
    category_slug: str
    weight:        float = 1.0


@dataclass
class AppendRequest:
    tenant_id:       str
    chunks:          list[ChunkIn]
    new_rules:       list[RuleIn] = field(default_factory=list)
    ml_fallback:     str = "auto"          # "auto" | "always" | "never"
    include_vectors: bool = False


@dataclass
class AppendChunkResult:
    chunk_ref:     str
    chunk_text:    str
    category_slug: str
    confidence:    float
    quality_score: float
    flagged:       bool
    embedding:     list[float] | None = None  # only when include_vectors=True


# ── Main entry point ──────────────────────────────────────────────────────────

def run_append(request: AppendRequest) -> list[AppendChunkResult]:
    """
    Categorise and embed new chunks against the tenant's active mapping.

    Steps:
      1. Load active mapping_id + rules + categories + MLP weights from DB.
      2. Merge any new_rules the client provided.
      3. Fine-tune final MLP layer on the extended rule set (if new_rules given).
      4. Embed chunk texts with Gemini (768-d).
      5. Project through MLP → 256-d personal embeddings.
      6. Assign category via centroid similarity + softmax confidence.
      7. Quality-score every chunk.
      8. ML fallback: for flagged (or all, if "always") chunks, run rule lookup
         and keep the higher-confidence assignment.
      9. UPSERT chunks to DB.
     10. Return results.
    """
    tenant_id = request.tenant_id

    # 1. Load mapping state
    mapping_id, existing_rules, existing_cats, mlp_blob = _load_active_mapping(tenant_id)
    if not mapping_id:
        raise ValueError(
            f"No active mapping for tenant {tenant_id}. "
            "Submit a full ingest job first to create the initial mapping."
        )

    # 2. Merge new rules
    merged_rules = existing_rules + [
        {"word": r.word.strip().lower(), "category_slug": r.category_slug, "weight": r.weight}
        for r in request.new_rules
    ]
    all_cats = list(dict.fromkeys(existing_cats + [r.category_slug for r in request.new_rules]))

    refs  = [c.ref  for c in request.chunks]
    texts = [c.text for c in request.chunks]

    # 3-5. Embed + project
    from prismrag.embedding.gemini import embed_texts
    sem_raw = embed_texts(texts)
    sem_arr = np.array([v if v is not None else [0.0] * 768 for v in sem_raw], dtype=float)

    if mlp_blob:
        from prismrag.mapping.mlp import load_mlp, _encode
        model = load_mlp(mlp_blob)
        if request.new_rules:
            model = _finetune_final_layer(model, merged_rules, request.new_rules)
        personal_embs = _encode(model, sem_arr)   # (N, 256)
    else:
        # No MLP — fall back to rules strategy projection
        logger.info("No MLP artifact for tenant %s — using RulesStrategy projection", tenant_id)
        return _rules_only_append(request, mapping_id, merged_rules, all_cats)

    # 6. Assign categories via centroid similarity
    centroids = _load_category_centroids(tenant_id, mapping_id)
    if not centroids and not all_cats:
        raise ValueError("No category centroids found — cannot assign categories.")

    categories, confidences = _assign_by_centroid(personal_embs, centroids, all_cats)

    # 7. Quality scoring
    quality = score_batch(refs, personal_embs, categories, confidences)

    # 8. ML fallback
    final_cats  = list(categories)
    final_confs = list(confidences)

    if request.ml_fallback in ("auto", "always"):
        for i, q in enumerate(quality):
            if request.ml_fallback == "always" or q.flagged:
                fb_cat, fb_conf = _rule_lookup(texts[i], merged_rules)
                if fb_cat and fb_conf > final_confs[i]:
                    final_cats[i]  = fb_cat
                    final_confs[i] = fb_conf

    # Recompute quality with final assignments
    final_quality = score_batch(refs, personal_embs, final_cats, final_confs)

    # 9. UPSERT to DB
    _upsert_chunks(
        tenant_id, mapping_id,
        refs, texts, final_cats,
        [personal_embs[i].tolist() for i in range(len(refs))],
        [sem_arr[i].tolist() for i in range(len(refs))],
    )

    # 10. Return results
    return [
        AppendChunkResult(
            chunk_ref=refs[i],
            chunk_text=texts[i],
            category_slug=final_cats[i],
            confidence=round(final_confs[i], 4),
            quality_score=q.quality_score,
            flagged=q.flagged,
            embedding=personal_embs[i].tolist() if request.include_vectors else None,
        )
        for i, q in enumerate(final_quality)
    ]


# ── DB helpers ────────────────────────────────────────────────────────────────

def _load_active_mapping(tenant_id: str):
    """Returns (mapping_id, rules, categories, mlp_blob) for the active mapping."""
    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT id FROM prismrag.mapping_version "
            "WHERE tenant_id = %s AND status = 'active' "
            "ORDER BY version DESC LIMIT 1",
            (tenant_id,),
        )
        row = cur.fetchone()
        if not row:
            return None, [], [], None
        mapping_id = str(row[0])

        cur.execute(
            "SELECT word, category_slug, weight FROM prismrag.mapping_rule WHERE mapping_id = %s",
            (mapping_id,),
        )
        rules = [{"word": r[0], "category_slug": r[1], "weight": float(r[2])} for r in cur.fetchall()]

        cur.execute(
            "SELECT category_slug FROM prismrag.mapping_category WHERE mapping_id = %s ORDER BY sort_order",
            (mapping_id,),
        )
        cats = [r[0] for r in cur.fetchall()]

        cur.execute(
            "SELECT weights_blob FROM prismrag.mlp_artifact "
            "WHERE tenant_id = %s AND mapping_id = %s",
            (tenant_id, mapping_id),
        )
        mlp_row = cur.fetchone()
        mlp_blob = bytes(mlp_row[0]) if mlp_row else None

        return mapping_id, rules, cats, mlp_blob
    finally:
        release_conn(conn)


def _load_category_centroids(tenant_id: str, mapping_id: str) -> dict[str, np.ndarray]:
    """Compute category centroids by averaging existing chunk embeddings."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT category_slug, avg(embedding)::text
            FROM prismrag.chunk_embedding
            WHERE tenant_id = %s AND mapping_id = %s
            GROUP BY category_slug
            """,
            (tenant_id, mapping_id),
        )
        import json
        centroids: dict[str, np.ndarray] = {}
        for slug, avg_text in cur.fetchall():
            if avg_text:
                vec = np.array(json.loads(avg_text), dtype=float)
                norm = np.linalg.norm(vec)
                centroids[slug] = vec / norm if norm > 1e-8 else vec
        return centroids
    finally:
        release_conn(conn)


def _upsert_chunks(
    tenant_id:  str,
    mapping_id: str,
    refs:       list[str],
    texts:      list[str],
    cats:       list[str],
    embs:       list[list[float]],
    sem_embs:   list[list[float] | None],
) -> None:
    from prismrag.db import vector_to_pg
    conn = get_conn()
    try:
        cur = conn.cursor()
        for i in range(len(refs)):
            cur.execute(
                """
                INSERT INTO prismrag.chunk_embedding
                    (tenant_id, mapping_id, chunk_text, chunk_ref, category_slug,
                     embedding, sem_embedding, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s::vector, %s::vector, '{}'::jsonb)
                ON CONFLICT (tenant_id, mapping_id, chunk_ref) DO UPDATE SET
                    chunk_text    = EXCLUDED.chunk_text,
                    category_slug = EXCLUDED.category_slug,
                    embedding     = EXCLUDED.embedding,
                    sem_embedding = EXCLUDED.sem_embedding
                """,
                (
                    tenant_id, mapping_id, texts[i], refs[i], cats[i],
                    vector_to_pg(embs[i]),
                    vector_to_pg(sem_embs[i]) if sem_embs[i] else None,
                ),
            )
        conn.commit()
    finally:
        release_conn(conn)


# ── Assignment helpers ────────────────────────────────────────────────────────

def _assign_by_centroid(
    embeddings: np.ndarray,
    centroids:  dict[str, np.ndarray],
    fallback_cats: list[str],
) -> tuple[list[str], list[float]]:
    """Assign each embedding to the closest centroid. Returns (categories, softmax_confidences)."""
    if not centroids:
        default = fallback_cats[0] if fallback_cats else "unknown"
        return [default] * len(embeddings), [0.0] * len(embeddings)

    cat_names = list(centroids.keys())
    cent_mat  = np.array([centroids[c] for c in cat_names], dtype=float)

    norms_e = np.linalg.norm(embeddings, axis=1, keepdims=True).clip(min=1e-8)
    norms_c = np.linalg.norm(cent_mat,   axis=1, keepdims=True).clip(min=1e-8)
    embs_n  = embeddings / norms_e
    cent_n  = cent_mat   / norms_c

    sims      = embs_n @ cent_n.T          # (N, C)
    best_idx  = np.argmax(sims, axis=1)    # (N,)

    # Softmax confidence
    exp_s = np.exp(sims - sims.max(axis=1, keepdims=True))
    probs  = exp_s / exp_s.sum(axis=1, keepdims=True)
    confs  = probs[np.arange(len(embeddings)), best_idx]

    return [cat_names[i] for i in best_idx], confs.tolist()


def _rule_lookup(text: str, rules: list[dict]) -> tuple[str | None, float]:
    """Keyword rule lookup: score text against all rules, return best category + confidence."""
    text_lower = text.lower()
    cat_scores: dict[str, float] = {}
    for rule in rules:
        if rule["word"] in text_lower:
            cat = rule["category_slug"]
            cat_scores[cat] = cat_scores.get(cat, 0.0) + rule.get("weight", 1.0)
    if not cat_scores:
        return None, 0.0
    total    = sum(cat_scores.values())
    best_cat = max(cat_scores, key=lambda c: cat_scores[c])
    return best_cat, cat_scores[best_cat] / total


# ── Fallback: rules-only (no MLP available) ────────────────────────────────────

def _rules_only_append(
    request: AppendRequest,
    mapping_id: str,
    rules: list[dict],
    cats: list[str],
) -> list[AppendChunkResult]:
    """Use RulesStrategy when no MLP artifact exists for this tenant."""
    from prismrag.models import MappingConfigIn, CategoryIn, MappingRuleIn
    from prismrag.mapping.rules import RulesStrategy

    cats_in  = [CategoryIn(slug=c, label=c, sort_order=i) for i, c in enumerate(cats)]
    rules_in = [
        MappingRuleIn(word=r["word"], category_slug=r["category_slug"], weight=r.get("weight", 1.0))
        for r in rules
    ]
    config   = MappingConfigIn(categories=cats_in, rules=rules_in)
    strategy = RulesStrategy(config)

    refs  = [c.ref  for c in request.chunks]
    texts = [c.text for c in request.chunks]

    batch_in  = [(c.ref, c.text, None) for c in request.chunks]
    batch_out = strategy.assign_batch(batch_in)

    personal_embs = np.array([r.embedding for r in batch_out], dtype=float)
    cats_out      = [r.category_slug for r in batch_out]

    quality = score_batch(refs, personal_embs, cats_out)

    _upsert_chunks(
        request.tenant_id, mapping_id, refs, texts, cats_out,
        [e.tolist() for e in personal_embs],
        [None] * len(refs),
    )

    return [
        AppendChunkResult(
            chunk_ref=refs[i],
            chunk_text=texts[i],
            category_slug=cats_out[i],
            confidence=0.0,   # rules has no probabilistic confidence
            quality_score=q.quality_score,
            flagged=q.flagged,
            embedding=personal_embs[i].tolist() if request.include_vectors else None,
        )
        for i, q in enumerate(quality)
    ]


# ── MLP fine-tune (freeze-then-finetune last layer only) ──────────────────────

def _finetune_final_layer(model, all_rules: list[dict], new_rules: list[dict], epochs: int = 40):
    """
    Fine-tune only the final linear layer of an existing MLP on the extended rule set.
    Freezing earlier layers prevents catastrophic forgetting of existing category structure.
    """
    try:
        import torch
        import torch.optim as optim
        from collections import defaultdict
        from prismrag.embedding.gemini import embed_texts
        from prismrag.config import MLP_TEMPERATURE
        from prismrag.mapping.mlp import _infonce_loss

        by_cat: dict[str, list[str]] = defaultdict(list)
        for rule in all_rules:
            by_cat[rule["category_slug"]].append(rule["word"])

        pairs = [
            (a, b)
            for words in by_cat.values()
            for i, a in enumerate(words)
            for j, b in enumerate(words)
            if i != j
        ]
        if not pairs:
            return model

        vocab   = list({w for p in pairs for w in p})
        sem_raw = embed_texts(vocab)
        sem_arr = np.array([v if v is not None else [0.0] * 768 for v in sem_raw], dtype=float)

        # Freeze all but the last Linear block (index 6 in the Sequential)
        for name, param in model.named_parameters():
            param.requires_grad = "net.6" in name

        trainable = [p for p in model.parameters() if p.requires_grad]
        if not trainable:
            return model

        model.train()
        opt = optim.Adam(trainable, lr=5e-5)
        for _ in range(epochs):
            opt.zero_grad()
            loss = _infonce_loss(model, pairs, sem_arr, vocab, MLP_TEMPERATURE, device=None)
            loss.backward()
            opt.step()

        model.eval()
        for param in model.parameters():
            param.requires_grad = True   # unfreeze for future calls

        return model
    except Exception as exc:
        logger.warning("Fine-tune skipped — using original MLP: %s", exc)
        return model
