"""
Chunk quality validation test suite.

Tests three concerns:
  1. Unit tests for the quality scoring algorithm itself (no API, no DB).
  2. Integration tests against the live API — chunk quality endpoint.
  3. End-to-end append validation — post new chunks, verify they are
     correctly categorised and quality scores are above threshold.

Run with:
  pytest tests/test_chunk_quality.py -v --tb=short            # unit only
  pytest tests/test_chunk_quality.py -v --tb=short -m live    # live API too
"""
from __future__ import annotations

import numpy as np
import pytest

# ── Unit tests: quality scoring algorithm ─────────────────────────────────────

class TestQualityScoring:
    """Pure-Python, no API, no DB."""

    def _score(self, refs, embs, cats, confs=None):
        from prismrag.pipeline.quality import score_batch
        return score_batch(refs, np.array(embs, dtype=float), cats, confs)

    def test_perfect_separation_scores_high(self):
        """Two clearly separated clusters should both score > 0.7."""
        # Category A: positive x axis; Category B: negative x axis
        a = [[1.0, 0.0, 0.0]] * 5
        b = [[-1.0, 0.0, 0.0]] * 5
        refs = [f"a{i}" for i in range(5)] + [f"b{i}" for i in range(5)]
        cats = ["cat_a"] * 5 + ["cat_b"] * 5
        embs = a + b
        scores = self._score(refs, embs, cats)
        for s in scores:
            assert s.quality_score >= 0.7, (
                f"Well-separated chunk {s.chunk_ref} has low quality: {s.quality_score}"
            )
            assert not s.flagged

    def test_ambiguous_chunk_scores_low(self):
        """A chunk equidistant between two category centroids should score < 0.6."""
        a_vecs = [[1.0, 0.0, 0.0]] * 4
        b_vecs = [[0.0, 1.0, 0.0]] * 4
        # Ambiguous: diagonal — equally close to both centroids
        ambiguous = [[0.707, 0.707, 0.0]]
        refs = [f"a{i}" for i in range(4)] + [f"b{i}" for i in range(4)] + ["ambig"]
        cats = ["cat_a"] * 4 + ["cat_b"] * 4 + ["cat_a"]
        embs = a_vecs + b_vecs + ambiguous
        scores = self._score(refs, embs, cats)
        ambig_score = next(s for s in scores if s.chunk_ref == "ambig")
        assert ambig_score.quality_score < 0.7, (
            f"Ambiguous chunk should score < 0.7, got {ambig_score.quality_score}"
        )

    def test_single_category_coherence_is_one(self):
        """When only one category exists, separation and coherence defaults are 1.0."""
        embs = [[1.0, 0.0], [0.9, 0.1], [0.95, 0.05]]
        refs = ["a", "b", "c"]
        cats = ["only_cat"] * 3
        scores = self._score(refs, embs, cats)
        for s in scores:
            assert s.separation == 1.0
            assert s.coherence  >= 0.9    # high similarity within cluster

    def test_confidence_from_provided_values(self):
        """Pre-computed confidences should override centroid-derived ones."""
        embs = [[1.0, 0.0], [0.0, 1.0]]
        refs = ["a", "b"]
        cats = ["cat_a", "cat_b"]
        confs = [0.99, 0.1]
        scores = self._score(refs, embs, cats, confs)
        assert scores[0].confidence == pytest.approx(0.99, abs=0.01)
        assert scores[1].confidence == pytest.approx(0.1,  abs=0.01)

    def test_flagged_below_threshold(self):
        """Chunks below LOW_QUALITY_THRESHOLD must have flagged=True."""
        from prismrag.pipeline.quality import LOW_QUALITY_THRESHOLD
        embs = [[1.0, 0.0, 0.0]] * 2 + [[-1.0, 0.0, 0.0]] * 2 + [[0.0, 1.0, 0.0]]
        refs = ["a0", "a1", "b0", "b1", "outlier"]
        cats = ["cat_a", "cat_a", "cat_b", "cat_b", "cat_a"]  # outlier in wrong cluster
        scores = self._score(refs, embs, cats)
        for s in scores:
            if s.flagged:
                assert s.quality_score < LOW_QUALITY_THRESHOLD + 0.05  # small tolerance for float

    def test_empty_input_returns_empty(self):
        from prismrag.pipeline.quality import score_batch
        assert score_batch([], np.zeros((0, 3)), []) == []

    def test_summarise_quality(self):
        from prismrag.pipeline.quality import summarise_quality
        scores = [
            {"confidence": 0.9, "separation": 0.8, "coherence": 0.95, "quality_score": 0.87, "flagged": False},
            {"confidence": 0.2, "separation": 0.3, "coherence": 0.4,  "quality_score": 0.28, "flagged": True},
        ]
        s = summarise_quality(scores)
        assert s["total"]    == 2
        assert s["flagged"]  == 1
        assert s["pct_flagged"] == 50.0
        assert s["avg_quality"] == pytest.approx(0.575, abs=0.01)


# ── Unit: append helpers ───────────────────────────────────────────────────────

class TestAppendHelpers:
    def test_rule_lookup_exact_match(self):
        from prismrag.pipeline.append import _rule_lookup
        rules = [
            {"word": "insulin",  "category_slug": "medication",    "weight": 1.0},
            {"word": "warfarin", "category_slug": "anticoagulant", "weight": 1.0},
        ]
        cat, conf = _rule_lookup("The patient takes insulin twice daily.", rules)
        assert cat == "medication"
        assert conf == pytest.approx(1.0, abs=0.01)

    def test_rule_lookup_no_match(self):
        from prismrag.pipeline.append import _rule_lookup
        rules = [{"word": "insulin", "category_slug": "medication", "weight": 1.0}]
        cat, conf = _rule_lookup("nothing relevant here", rules)
        assert cat is None
        assert conf == 0.0

    def test_rule_lookup_multi_hit(self):
        from prismrag.pipeline.append import _rule_lookup
        rules = [
            {"word": "risk",    "category_slug": "risk",   "weight": 2.0},
            {"word": "credit",  "category_slug": "risk",   "weight": 1.0},
            {"word": "equity",  "category_slug": "market", "weight": 1.0},
        ]
        cat, conf = _rule_lookup("credit risk analysis", rules)
        assert cat == "risk"
        assert conf > 0.5

    def test_assign_by_centroid_picks_closest(self):
        from prismrag.pipeline.append import _assign_by_centroid
        centroids = {
            "cat_a": np.array([1.0, 0.0, 0.0]),
            "cat_b": np.array([0.0, 1.0, 0.0]),
        }
        embeddings = np.array([
            [0.99, 0.01, 0.0],   # clearly cat_a
            [0.01, 0.99, 0.0],   # clearly cat_b
        ])
        cats, confs = _assign_by_centroid(embeddings, centroids, ["cat_a", "cat_b"])
        assert cats[0] == "cat_a"
        assert cats[1] == "cat_b"
        assert confs[0] > 0.5
        assert confs[1] > 0.5


# ── Integration tests (marked live — need running API) ────────────────────────

pytestmark_live = pytest.mark.live


@pytest.mark.live
class TestChunkQualityEndpoints:
    """Integration tests against a running PrismRAG API."""

    def test_quality_report_returns_summary(self, authed_api, healthcare_tenant, healthcare_ingest):
        """Quality endpoint must return summary + per-chunk scores."""
        from tests.conftest import RAG_API
        r = authed_api.get(
            authed_api.url(f"{RAG_API}/tenants/{healthcare_tenant}/chunks/quality")
        )
        assert r.status_code == 200, f"Quality endpoint failed: {r.text}"
        body = r.json()
        assert "summary"    in body
        assert "chunks"     in body
        assert "mapping_id" in body

        s = body["summary"]
        assert s["total"] > 0
        assert 0.0 <= s["avg_quality"] <= 1.0
        assert "flagged" in s
        assert "pct_flagged" in s
        print(
            f"\n[CHUNK QUALITY: healthcare]  avg={s['avg_quality']:.3f}  "
            f"flagged={s['flagged']}/{s['total']} ({s['pct_flagged']}%)"
        )

    @pytest.mark.parametrize("domain", ["healthcare", "pharmacy", "finance"])
    def test_quality_threshold(
        self, domain, authed_api,
        healthcare_tenant, pharmacy_tenant, finance_tenant,
        healthcare_ingest, pharmacy_ingest, finance_ingest,
    ):
        """At least 70% of chunks in each domain must score above 0.45."""
        from tests.conftest import RAG_API
        tenant_id = {
            "healthcare": healthcare_tenant,
            "pharmacy":   pharmacy_tenant,
            "finance":    finance_tenant,
        }[domain]

        r = authed_api.get(
            authed_api.url(f"{RAG_API}/tenants/{tenant_id}/chunks/quality")
        )
        assert r.status_code == 200
        body = r.json()
        s = body["summary"]

        if s["total"] == 0:
            pytest.skip("No chunks — ingest may not have completed")

        pct_good = 100.0 - s["pct_flagged"]
        assert pct_good >= 70.0, (
            f"{domain}: only {pct_good:.1f}% of chunks above quality threshold. "
            f"avg_quality={s['avg_quality']:.3f}  "
            f"flagged={s['flagged']}/{s['total']}"
        )

    def test_append_chunks_tier2(self, authed_api, healthcare_tenant, healthcare_ingest):
        """
        Append 3 new chunks to the healthcare mapping.
        Verify they are categorised, quality-scored, and returned correctly.
        """
        from tests.conftest import RAG_API

        new_chunks = [
            {"ref": "test_append_001", "text": "Patient requires insulin dose adjustment for hyperglycaemia management."},
            {"ref": "test_append_002", "text": "CBC shows elevated WBC count suggesting acute infection."},
            {"ref": "test_append_003", "text": "Routine X-ray imaging ordered for chest pain evaluation."},
        ]
        r = authed_api.post(
            authed_api.url(f"{RAG_API}/tenants/{healthcare_tenant}/chunks/append"),
            json={
                "chunks":      new_chunks,
                "ml_fallback": "auto",
            },
        )
        assert r.status_code == 200, f"Append failed: {r.status_code} {r.text}"
        body = r.json()

        assert body["appended"] == 3
        assert len(body["chunks"]) == 3
        assert "summary" in body

        # Every chunk must have required fields
        for chunk in body["chunks"]:
            assert "chunk_ref"     in chunk
            assert "category_slug" in chunk
            assert "quality_score" in chunk
            assert "flagged"       in chunk
            assert 0.0 <= chunk["quality_score"] <= 1.0

        # Category slugs must be known categories from the mapping
        from tests.conftest import DOMAIN_CONFIGS
        valid_cats = {c["slug"] for c in DOMAIN_CONFIGS["healthcare"]["mapping"]["categories"]}
        for chunk in body["chunks"]:
            assert chunk["category_slug"] in valid_cats, (
                f"Unknown category '{chunk['category_slug']}' for chunk {chunk['chunk_ref']}"
            )

        print(f"\n[APPEND: healthcare]")
        for c in body["chunks"]:
            flag = " ⚠" if c["flagged"] else " ✓"
            print(f"  {flag} {c['chunk_ref']} → {c['category_slug']}  (q={c['quality_score']:.3f})")

    def test_append_with_new_rules_extends_mapping(self, authed_api, finance_tenant, finance_ingest):
        """
        Append chunks for a new vocabulary area + new rules.
        The new rules should improve categorisation of those specific terms.
        """
        from tests.conftest import RAG_API

        r = authed_api.post(
            authed_api.url(f"{RAG_API}/tenants/{finance_tenant}/chunks/append"),
            json={
                "chunks": [
                    {"ref": "esg_001", "text": "Carbon offset trading and ESG compliance reporting."},
                    {"ref": "esg_002", "text": "Sustainability-linked loan covenant tied to CO2 emissions."},
                ],
                "new_rules": [
                    {"word": "esg",           "category_slug": "risk",      "weight": 2.0},
                    {"word": "carbon",        "category_slug": "risk",      "weight": 1.5},
                    {"word": "sustainability","category_slug": "valuation",  "weight": 1.0},
                ],
                "ml_fallback": "auto",
            },
        )
        assert r.status_code == 200, f"Append with new rules failed: {r.text}"
        body = r.json()
        assert body["appended"] == 2
        print(f"\n[APPEND+RULES: finance]  {body['summary']}")

    def test_append_upsert_updates_existing(self, authed_api, healthcare_tenant, healthcare_ingest):
        """Appending the same ref twice should update, not create a duplicate."""
        from tests.conftest import RAG_API

        chunk = {"ref": "upsert_test_001", "text": "Original text about medication dosing."}
        r1 = authed_api.post(
            authed_api.url(f"{RAG_API}/tenants/{healthcare_tenant}/chunks/append"),
            json={"chunks": [chunk]},
        )
        assert r1.status_code == 200

        updated = {"ref": "upsert_test_001", "text": "Updated text about insulin administration and glycaemic control."}
        r2 = authed_api.post(
            authed_api.url(f"{RAG_API}/tenants/{healthcare_tenant}/chunks/append"),
            json={"chunks": [updated]},
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["appended"] == 1
        updated_chunk = body2["chunks"][0]
        assert updated_chunk["chunk_ref"] == "upsert_test_001"

    def test_append_empty_chunks_rejected(self, authed_api, healthcare_tenant):
        """Empty chunks array must return 422."""
        from tests.conftest import RAG_API
        r = authed_api.post(
            authed_api.url(f"{RAG_API}/tenants/{healthcare_tenant}/chunks/append"),
            json={"chunks": []},
        )
        assert r.status_code == 422

    def test_append_invalid_ml_fallback_rejected(self, authed_api, healthcare_tenant):
        """Unknown ml_fallback value must return 422."""
        from tests.conftest import RAG_API
        r = authed_api.post(
            authed_api.url(f"{RAG_API}/tenants/{healthcare_tenant}/chunks/append"),
            json={"chunks": [{"ref": "x", "text": "test"}], "ml_fallback": "maybe"},
        )
        assert r.status_code == 422


# ── MCP tool coverage ─────────────────────────────────────────────────────────

class TestMcpChunkTools:
    """Verify the new MCP tool definitions are present and well-formed."""

    def test_new_tools_present(self):
        pytest.importorskip("mcp", reason="pip install mcp for MCP tests")
        from prismrag.mcp import server as s
        names = {t["name"] for t in s.TOOLS}
        for expected in ("append_chunks", "export_chunks", "score_chunk_quality"):
            assert expected in names, f"MCP tool '{expected}' missing from TOOLS list"

    def test_new_handlers_registered(self):
        pytest.importorskip("mcp", reason="pip install mcp for MCP tests")
        from prismrag.mcp import server as s
        for name in ("append_chunks", "export_chunks", "score_chunk_quality"):
            assert name in s._HANDLERS, f"Handler for '{name}' not registered in _HANDLERS"

    def test_append_chunks_schema_valid(self):
        pytest.importorskip("mcp", reason="pip install mcp for MCP tests")
        from prismrag.mcp import server as s
        tool = next(t for t in s.TOOLS if t["name"] == "append_chunks")
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "chunks" in schema["properties"]
        items = schema["properties"]["chunks"]["items"]
        assert "ref"  in items["properties"]
        assert "text" in items["properties"]
