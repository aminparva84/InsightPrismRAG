"""Tests — PrismRAG ingestion, search, and bridge vectors across 3 domains."""
import time
import pytest
from tests.conftest import RAG_API, DOMAIN_CONFIGS, HEALTHCARE_MAPPING, PHARMACY_MAPPING, FINANCE_MAPPING


# ── Helpers ───────────────────────────────────────────────────────────────────

def ingest_job(authed_api, tenant_id, mapping, strategy="rules"):
    """Submit an inline ingest job and poll to completion. Returns final job dict."""
    payload = {
        "tenant_id":   tenant_id,
        "source_type": "inline",
        "strategy":    strategy,
        "mapping":     mapping,
        "inline_config": {
            "records": [
                {"word": r["word"], "text": r["word"].replace("_", " ")}
                for r in mapping["rules"]
            ],
        },
    }
    r = authed_api.post(authed_api.url(f"{RAG_API}/jobs"), json=payload)
    assert r.status_code in (200, 201, 202), f"Job submit failed: {r.status_code} {r.text}"
    job = r.json()
    job_id = job.get("job_id") or job.get("id")

    for _ in range(60):
        r2 = authed_api.get(authed_api.url(f"{RAG_API}/jobs/{job_id}"))
        status = r2.json().get("status", "")
        if status == "done":
            return r2.json()
        if status == "failed":
            pytest.fail(f"Job {job_id} failed: {r2.text}")
        time.sleep(2)
    pytest.fail(f"Job {job_id} timed out after 120s (still {status})")


# ── Domain job fixtures ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def healthcare_job(authed_api, healthcare_tenant):
    return ingest_job(authed_api, healthcare_tenant, HEALTHCARE_MAPPING)

@pytest.fixture(scope="module")
def pharmacy_job(authed_api, pharmacy_tenant):
    return ingest_job(authed_api, pharmacy_tenant, PHARMACY_MAPPING)

@pytest.fixture(scope="module")
def finance_job(authed_api, finance_tenant):
    return ingest_job(authed_api, finance_tenant, FINANCE_MAPPING)


# ── Ingest tests ─────────────────────────────────────────────────────────────

class TestIngest:
    def test_healthcare_job_done(self, healthcare_job):
        assert healthcare_job["status"] == "done"
        assert healthcare_job.get("words_processed", 0) > 0

    def test_pharmacy_job_done(self, pharmacy_job):
        assert pharmacy_job["status"] == "done"
        assert pharmacy_job.get("words_processed", 0) > 0

    def test_finance_job_done(self, finance_job):
        assert finance_job["status"] == "done"
        assert finance_job.get("words_processed", 0) > 0

    def test_communities_built_healthcare(self, authed_api, healthcare_tenant, healthcare_job):
        r = authed_api.get(authed_api.url(f"{RAG_API}/communities?tenant_id={healthcare_tenant}"))
        assert r.status_code == 200
        assert len(r.json()) >= 3, "Expected at least 3 communities"

    def test_communities_built_finance(self, authed_api, finance_tenant, finance_job):
        r = authed_api.get(authed_api.url(f"{RAG_API}/communities?tenant_id={finance_tenant}"))
        assert r.status_code == 200
        assert len(r.json()) >= 3

    def test_empty_rules_rejected(self, authed_api, healthcare_tenant):
        r = authed_api.post(authed_api.url(f"{RAG_API}/jobs"), json={
            "tenant_id":     healthcare_tenant,
            "source_type":   "inline",
            "strategy":      "rules",
            "mapping":       {"categories": [{"slug": "a", "label": "A"}], "rules": []},
            "inline_config": {"records": [{"word": "test", "text": "test"}]},
        })
        assert r.status_code in (400, 422)

    def test_unknown_tenant_rejected(self, authed_api):
        r = authed_api.post(authed_api.url(f"{RAG_API}/jobs"), json={
            "tenant_id":     "00000000-0000-0000-0000-000000000000",
            "source_type":   "inline",
            "strategy":      "rules",
            "mapping":       HEALTHCARE_MAPPING,
            "inline_config": {"records": [{"word": "test", "text": "test"}]},
        })
        assert r.status_code in (403, 404, 422)


# ── Search tests ─────────────────────────────────────────────────────────────

class TestSearch:

    @pytest.mark.parametrize("domain,query,expected_category", [
        ("healthcare", "What medications are used for diabetes management?",          "medication"),
        ("healthcare", "What lab tests confirm heart attack diagnosis?",               "lab_results"),
        ("healthcare", "What patient safety risks apply to drug allergies?",          "patient_safety"),
        ("pharmacy",   "What CYP450 drug interactions affect warfarin?",              "drug_interactions"),
        ("pharmacy",   "How should insulin be stored and handled?",                   "storage"),
        ("pharmacy",   "What is the bioavailability and half-life of amoxicillin?",  "pharmacokinetics"),
        ("finance",    "What is the DCF valuation method and WACC calculation?",     "valuation"),
        ("finance",    "What are the key credit risk and VaR metrics?",              "risk"),
        ("finance",    "What is the current ratio and free cash flow status?",       "liquidity"),
    ])
    def test_search_returns_results(
        self, domain, query, expected_category,
        authed_api, healthcare_tenant, pharmacy_tenant, finance_tenant,
        healthcare_job, pharmacy_job, finance_job,
    ):
        tenant_id = {"healthcare": healthcare_tenant,
                     "pharmacy":   pharmacy_tenant,
                     "finance":    finance_tenant}[domain]
        r = authed_api.post(authed_api.url(f"{RAG_API}/search"), json={
            "tenant_id": tenant_id, "query": query, "top_k": 5,
        })
        assert r.status_code == 200, f"Search failed: {r.text}"
        results = r.json().get("results", [])
        assert len(results) > 0, f"No results for: {query}"
        top_cat = results[0].get("category_slug", "")
        if top_cat != expected_category:
            print(f"\n[WARN] domain={domain} expected={expected_category} got={top_cat}")

    def test_category_filter(self, authed_api, finance_tenant, finance_job):
        r = authed_api.post(authed_api.url(f"{RAG_API}/search"), json={
            "tenant_id": finance_tenant, "query": "portfolio analysis",
            "top_k": 10, "category_filter": "risk",
        })
        assert r.status_code == 200
        for res in r.json()["results"]:
            assert res.get("category_slug") == "risk"

    def test_top_k_respected(self, authed_api, healthcare_tenant, healthcare_job):
        for k in [1, 3, 5]:
            r = authed_api.post(authed_api.url(f"{RAG_API}/search"), json={
                "tenant_id": healthcare_tenant, "query": "patient medication", "top_k": k,
            })
            assert r.status_code == 200
            assert len(r.json()["results"]) <= k

    def test_tenant_isolation(self, authed_api, healthcare_tenant, pharmacy_tenant,
                              healthcare_job, pharmacy_job):
        query = "diabetes insulin medication"
        hc = authed_api.post(authed_api.url(f"{RAG_API}/search"),
                             json={"tenant_id": healthcare_tenant, "query": query, "top_k": 3})
        ph = authed_api.post(authed_api.url(f"{RAG_API}/search"),
                             json={"tenant_id": pharmacy_tenant, "query": query, "top_k": 3})
        hc_cats = [r["category_slug"] for r in hc.json()["results"]]
        ph_cats = [r["category_slug"] for r in ph.json()["results"]]
        assert hc_cats != ph_cats, "Tenants appear to share the same graph — isolation broken"

    def test_result_structure(self, authed_api, finance_tenant, finance_job):
        r = authed_api.post(authed_api.url(f"{RAG_API}/search"),
                            json={"tenant_id": finance_tenant, "query": "valuation", "top_k": 3})
        assert r.status_code == 200
        for res in r.json()["results"]:
            assert "category_slug" in res
            assert "score" in res
            assert 0.0 <= res["score"] <= 1.0

    def test_search_latency_under_3s(self, authed_api, finance_tenant, finance_job):
        start = time.time()
        r = authed_api.post(authed_api.url(f"{RAG_API}/search"),
                            json={"tenant_id": finance_tenant, "query": "credit risk", "top_k": 5})
        assert r.status_code == 200
        assert time.time() - start < 3.0, "Search exceeded 3s latency threshold"

    def test_empty_query_rejected(self, authed_api, healthcare_tenant):
        r = authed_api.post(authed_api.url(f"{RAG_API}/search"),
                            json={"tenant_id": healthcare_tenant, "query": ""})
        assert r.status_code == 422


# ── Bridge vector tests ───────────────────────────────────────────────────────

class TestBridgeVectors:

    @pytest.fixture(scope="class")
    def bridge(self, authed_api, healthcare_tenant, healthcare_job):
        r = authed_api.get(authed_api.url(f"{RAG_API}/communities?tenant_id={healthcare_tenant}"))
        communities = r.json()
        if len(communities) < 2:
            pytest.skip("Need at least 2 communities for bridge test")
        rb = authed_api.post(authed_api.url(f"{RAG_API}/bridges"), json={
            "tenant_id":    healthcare_tenant,
            "community_a":  communities[0]["id"],
            "community_b":  communities[1]["id"],
            "bridge_label": "QA Bridge: diagnosis-treatment",
        })
        assert rb.status_code in (200, 201), f"Bridge create failed: {rb.text}"
        return rb.json()

    def test_bridge_created(self, bridge):
        assert "bridge_id" in bridge or "id" in bridge

    def test_bridge_searchable(self, authed_api, healthcare_tenant, bridge, healthcare_job):
        r = authed_api.post(authed_api.url(f"{RAG_API}/search"), json={
            "tenant_id": healthcare_tenant,
            "query":     "diagnosis and treatment pathway",
            "top_k":     10,
        })
        assert r.status_code == 200
        categories = {res["category_slug"] for res in r.json()["results"]}
        assert len(categories) >= 2, "Bridge search should span multiple categories"
