"""Integration tests for prismrag-patch — run after pip install."""
from __future__ import annotations

import time

import pytest

from mapping import DEMO_MAPPING, demo_records
from prismrag_patch import PrismRAG, PrismRAGPatch


@pytest.fixture(scope="module")
def rag() -> PrismRAG:
    client = PrismRAG(mapping=DEMO_MAPPING, tenant_id="demo-app-test")
    job = client.ingest(records=demo_records())
    assert job["status"] == "completed"
    return client


class TestPackageImport:
    def test_import_prismrag(self):
        assert PrismRAG is not None

    def test_no_license_key_required(self):
        patch = PrismRAGPatch(mapping=DEMO_MAPPING)
        cat = patch.category_for("metformin diabetes")
        slug = cat["slug"] if isinstance(cat, dict) else cat
        assert slug == "medication"


class TestIngestPipeline:
    def test_ingest_writes_all_rules(self, rag: PrismRAG):
        chunks = rag.export_chunks()
        assert len(chunks) == len(DEMO_MAPPING["rules"])

    def test_dual_vectors_present(self, rag: PrismRAG):
        chunk = rag.export_chunks()[0]
        assert len(chunk["embedding"]) == 256
        assert len(chunk["sem_embedding"]) == 768
        slugs = {c["slug"] for c in DEMO_MAPPING["categories"]}
        assert chunk["category_slug"] in slugs

    def test_communities_and_graph(self, rag: PrismRAG):
        comms = rag.list_communities()
        assert len(comms) >= 1
        assert len(rag.export_chunks()) == len(DEMO_MAPPING["rules"])


class TestSearch:
    def test_search_returns_hits(self, rag: PrismRAG):
        data = rag.search("diabetes medication metformin", top_k=5)
        assert len(data["results"]) > 0
        assert data["retrieval_mode"] in ("graph_rag", "direct")

    def test_medication_query_prefers_medication_category(self, rag: PrismRAG):
        data = rag.search("What medications for diabetes?", top_k=5)
        categories = {r["category_slug"] for r in data["results"]}
        assert "medication" in categories

    def test_category_filter(self, rag: PrismRAG):
        data = rag.search("heart attack lab test", top_k=10, category_filter="lab_results")
        for res in data["results"]:
            assert res["category_slug"] == "lab_results"

    def test_top_k_respected(self, rag: PrismRAG):
        for k in (1, 3):
            data = rag.search("fever symptoms", top_k=k)
            assert len(data["results"]) <= k

    def test_search_under_3_seconds(self, rag: PrismRAG):
        start = time.time()
        rag.search("troponin cardiac marker", top_k=5)
        assert time.time() - start < 3.0


class TestAppendAndQuality:
    def test_append_chunk(self, rag: PrismRAG):
        out = rag.append_chunks(
            chunks=[{"ref": "headache", "text": "severe headache presentation"}],
            new_rules=[{"word": "headache", "category_slug": "symptoms", "weight": 1.0}],
        )
        assert out[0]["chunk_ref"] == "headache"
        assert 0.0 <= out[0]["quality_score"] <= 1.0

    def test_chunk_quality_report(self, rag: PrismRAG):
        report = rag.chunk_quality()
        assert report["summary"]["total"] >= len(DEMO_MAPPING["rules"])
        assert report["summary"]["avg_quality"] is not None


class TestBridge:
    def test_create_bridge_when_multiple_communities(self, rag: PrismRAG):
        comms = rag.list_communities()
        if len(comms) < 2:
            pytest.skip("single community in mini mapping")
        bridge = rag.create_bridge(
            comms[0]["community_id"],
            comms[1]["community_id"],
        )
        assert "bridge_id" in bridge
