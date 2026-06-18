"""PrismRAG — Tier 1: RulesStrategy.

Explicit word→category mapping. Fully auditable: every assignment
traces back to a row in prismrag.mapping_rule.

Vector production:
  1. Embed each word with Gemini (cached).
  2. Project through a learned category centroid offset so that words
     in the same category cluster together in 256-d space.
     Without an MLP, we use a simple PCA-like linear projection that
     aligns category centroids along orthogonal axes.
  3. L2-normalise to unit sphere.
"""
from __future__ import annotations

import logging
from uuid import UUID

import numpy as np

from prismrag.embedding.gemini import embed_texts
from prismrag.mapping.base import MappingResult, MappingStrategy
from prismrag.models import MappingConfigIn

logger = logging.getLogger(__name__)


class RulesStrategy(MappingStrategy):
    """
    Tier-1 mapping strategy.

    Params
    ------
    mapping_config : MappingConfigIn
        Category definitions + word→category rules.
    embed_dim : int
        Output embedding dimension (default 256).
    """

    def __init__(self, mapping_config: MappingConfigIn, embed_dim: int = 256):
        self._rules: dict[str, str] = {
            r.word.strip().lower(): r.category_slug
            for r in mapping_config.rules
        }
        self._categories: list[str] = [c.slug for c in mapping_config.categories]
        self._embed_dim  = embed_dim
        self._cat_index: dict[str, int] = {slug: i for i, slug in enumerate(self._categories)}
        self._projection: np.ndarray | None = None  # built lazily

    # ── Public API ────────────────────────────────────────────────────────────

    def assign(
        self, word: str, text: str, category_hint: str | None = None
    ) -> MappingResult:
        result = self.assign_batch([(word, text, category_hint)])
        return result[0]

    def assign_batch(
        self, records: list[tuple[str, str, str | None]]
    ) -> list[MappingResult]:
        words     = [r[0].strip().lower() for r in records]
        texts     = [r[1] for r in records]
        hints     = [r[2] for r in records]
        cat_slugs = [self._lookup_category(w, h) for w, h in zip(words, hints)]

        # Get Gemini 768-d vectors
        sem_vecs = embed_texts(texts)

        failed = [texts[i] for i, v in enumerate(sem_vecs) if v is None]
        if failed:
            raise RuntimeError(
                f"Gemini embedding failed for {len(failed)} chunk(s) — cannot store zero vectors. "
                f"Check GEMINI_API_KEY and network connectivity. First failed text: {failed[0][:80]!r}"
            )

        results = []
        for i, (cat, sem_vec) in enumerate(zip(cat_slugs, sem_vecs)):
            sem_arr = np.array(sem_vec, dtype=float)

            # Project 768-d → 256-d with category-aware linear map
            personal_vec = self._project(sem_arr, cat)
            results.append(MappingResult(
                category_slug=cat,
                embedding=personal_vec,
                sem_embedding=sem_arr,
            ))
        return results

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _lookup_category(self, word: str, hint: str | None) -> str:
        """Rule lookup → hint fallback → first category default."""
        if word in self._rules:
            return self._rules[word]
        if hint and hint in self._cat_index:
            return hint
        return self._categories[0] if self._categories else "default"

    def _project(self, sem_vec: np.ndarray, category_slug: str) -> np.ndarray:
        """
        Project 768-d semantic vector into 256-d personal space.

        Strategy:
          - The first N_cat dimensions encode a one-hot-like category signal
            (category centroid direction), so different categories are pulled
            apart even if Gemini places them close together.
          - The remaining dims are a random-but-fixed PCA projection of the
            semantic vector.

        This gives a deterministic, auditable projection that doesn't require
        MLP training — but still separates categories in the output space.
        The Tier-2 MLP will then refine these boundaries.
        """
        if self._projection is None:
            self._projection = self._build_projection()

        cat_idx = self._cat_index.get(category_slug, 0)
        n_cats  = max(len(self._categories), 1)

        # Category one-hot signal embedded into first n_cats dims of output
        cat_signal = np.zeros(self._embed_dim, dtype=float)
        cat_signal[cat_idx % self._embed_dim] = 1.0

        # Semantic projection
        sem_projected = self._projection @ sem_vec   # (256,)

        # Blend: 30% category signal, 70% semantic content
        blended = 0.30 * cat_signal + 0.70 * sem_projected

        # L2 normalise
        norm = np.linalg.norm(blended)
        return (blended / norm) if norm > 0 else blended

    def _build_projection(self) -> np.ndarray:
        """
        Build a fixed 256×768 random projection matrix (seeded for reproducibility).
        This is the same trick as random-indexing / LSH — preserves cosine distances.
        """
        rng = np.random.RandomState(seed=42)
        mat = rng.randn(self._embed_dim, 768).astype(float)
        # Orthonormalise rows for better distance preservation
        q, _ = np.linalg.qr(mat.T)
        return q.T[:self._embed_dim]  # (256, 768)

    # ── Persistence helpers ───────────────────────────────────────────────────

    @classmethod
    def from_db(cls, mapping_id: str | UUID, embed_dim: int = 256) -> "RulesStrategy":
        """Load a RulesStrategy from the DB mapping tables."""
        from prismrag.db import get_conn, release_conn
        from prismrag.models import MappingConfigIn, CategoryIn, MappingRuleIn
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT category_slug, category_label, sort_order "
                "FROM prismrag.mapping_category WHERE mapping_id = %s ORDER BY sort_order",
                (str(mapping_id),),
            )
            cats = [CategoryIn(slug=r[0], label=r[1], sort_order=r[2]) for r in cur.fetchall()]

            cur.execute(
                "SELECT word, category_slug, weight "
                "FROM prismrag.mapping_rule WHERE mapping_id = %s",
                (str(mapping_id),),
            )
            rules = [MappingRuleIn(word=r[0], category_slug=r[1], weight=r[2]) for r in cur.fetchall()]
        finally:
            release_conn(conn)

        return cls(MappingConfigIn(categories=cats, rules=rules), embed_dim=embed_dim)
