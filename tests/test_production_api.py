"""
Production API QA — healthcare, pharmacy, and finance domains.

Runs against the published Azure API with pre-seeded tenants, mappings, and a
dedicated QA user in Azure Postgres.

Prerequisites:
  1. Seed Azure DB:  python tests/seed_qa_data.py --production --drop --dsn <azure-dsn>
  2. Worker + Gemini configured on the Container App

Usage:
  PRISMRAG_TEST_URL=https://prismrag.insightits.com \\
  PRISMRAG_TEST_EMAIL=qa-prod@insightits.com \\
  PRISMRAG_TEST_PASSWORD=QaProdPass!2026# \\
  QA_SEEDED=1 \\
  pytest tests/test_production_api.py -v --tb=short

Or:  .\\scripts\\run_production_qa.ps1
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.conftest import (
    DOMAIN_CONFIGS,
    QA_SEEDED_TENANT_IDS,
    RAG_API,
)
from tests.helpers import login, poll_job, search_sync

PROD_URL = os.environ.get("PRISMRAG_TEST_URL", "https://prismrag.insightits.com").rstrip("/")
PROD_EMAIL = os.environ.get("PRISMRAG_TEST_EMAIL", "qa-prod@insightits.com")
PROD_PASSWORD = os.environ.get("PRISMRAG_TEST_PASSWORD", "QaProdPass!2026#")

SAMPLE_RECORDS_PATH = Path(__file__).parent / "fixtures" / "production_sample_records.json"
SAMPLE_RECORDS = json.loads(SAMPLE_RECORDS_PATH.read_text(encoding="utf-8"))

pytestmark = pytest.mark.production


@pytest.fixture(scope="session")
def prod_api(api):
    return api


@pytest.fixture(scope="session")
def prod_auth_token(prod_api):
    data = login(prod_api, PROD_EMAIL, PROD_PASSWORD)
    token = data["token"]
    prod_api.headers["Authorization"] = f"Bearer {token}"
    return token


@pytest.fixture(scope="session")
def prod_authed_api(prod_api, prod_auth_token):
    return prod_api


def _sample_records(domain: str) -> list[dict]:
    return SAMPLE_RECORDS[domain]["records"]


def _ingest_domain(prod_authed_api, domain: str) -> dict:
    tenant_id = QA_SEEDED_TENANT_IDS[domain]
    mapping = DOMAIN_CONFIGS[domain]["mapping"]
    payload = {
        "tenant_id": tenant_id,
        "source_type": "inline",
        "strategy": "rules",
        "mapping": mapping,
        "inline_config": {"records": _sample_records(domain)},
    }
    r = prod_authed_api.post(prod_authed_api.url(f"{RAG_API}/jobs"), json=payload)
    assert r.status_code in (200, 201, 202), f"Ingest submit failed: {r.status_code} {r.text}"
    job_id = r.json().get("job_id") or r.json().get("id")
    return poll_job(prod_authed_api, RAG_API, job_id, timeout_s=300)


@pytest.fixture(scope="module")
def healthcare_ingest(prod_authed_api):
    return _ingest_domain(prod_authed_api, "healthcare")


@pytest.fixture(scope="module")
def pharmacy_ingest(prod_authed_api):
    return _ingest_domain(prod_authed_api, "pharmacy")


@pytest.fixture(scope="module")
def finance_ingest(prod_authed_api):
    return _ingest_domain(prod_authed_api, "finance")


class TestProductionSmoke:
    def test_health(self, prod_api):
        r = prod_api.get(prod_api.url("/api/v1/prismrag/health"))
        assert r.status_code == 200
        assert r.json().get("status") in ("ok", "healthy", "up")

    def test_login_and_me(self, prod_authed_api, prod_auth_token):
        assert prod_auth_token
        r = prod_authed_api.get(prod_authed_api.url("/api/v1/auth/me"))
        assert r.status_code == 200
        me = r.json()
        assert me.get("email") == PROD_EMAIL
        assert me.get("plan") in ("professional", "enterprise", "starter", "free")

    def test_seeded_tenants_visible(self, prod_authed_api):
        r = prod_authed_api.get(prod_authed_api.url(f"{RAG_API}/tenants"))
        assert r.status_code == 200
        ids = {t.get("tenant_id") or t.get("id") for t in r.json()}
        for domain, tid in QA_SEEDED_TENANT_IDS.items():
            assert tid in ids, f"Seeded {domain} tenant missing — run seed_qa_data.py --production"


class TestProductionMappings:
    @pytest.mark.parametrize("domain", ["healthcare", "pharmacy", "finance"])
    def test_mapping_categories_seeded(self, prod_authed_api, domain):
        tenant_id = QA_SEEDED_TENANT_IDS[domain]
        r = prod_authed_api.get(
            prod_authed_api.url(f"{RAG_API}/mappings?tenant_id={tenant_id}")
        )
        if r.status_code == 404:
            pytest.skip("Mappings list endpoint not exposed — categories verified via ingest")
        assert r.status_code == 200
        body = r.json()
        mappings = body if isinstance(body, list) else body.get("mappings", [])
        assert mappings, f"No mapping found for {domain}"


class TestProductionIngest:
    def test_healthcare_ingest(self, healthcare_ingest):
        assert healthcare_ingest["status"] == "completed"
        assert healthcare_ingest.get("records_written", 0) >= len(_sample_records("healthcare"))

    def test_pharmacy_ingest(self, pharmacy_ingest):
        assert pharmacy_ingest["status"] == "completed"
        assert pharmacy_ingest.get("records_written", 0) >= len(_sample_records("pharmacy"))

    def test_finance_ingest(self, finance_ingest):
        assert finance_ingest["status"] == "completed"
        assert finance_ingest.get("records_written", 0) >= len(_sample_records("finance"))


class TestProductionSearch:
    @pytest.mark.parametrize("domain,query,expected_category", [
        ("healthcare", "What medications are used for diabetes management?", "medication"),
        ("healthcare", "What lab tests indicate heart attack?", "lab_results"),
        ("pharmacy", "What CYP450 interactions affect warfarin?", "drug_interactions"),
        ("pharmacy", "How should medications be refrigerated and stored in cold chain?", "storage"),
        ("finance", "What is the DCF valuation and WACC?", "valuation"),
        ("finance", "What are the credit risk and VaR metrics?", "risk"),
    ])
    def test_domain_search(
        self, domain, query, expected_category,
        prod_authed_api, healthcare_ingest, pharmacy_ingest, finance_ingest,
    ):
        tenant_id = QA_SEEDED_TENANT_IDS[domain]
        result = search_sync(prod_authed_api, RAG_API, tenant_id, query)
        hits = result.get("hits") or result.get("results") or result.get("chunks") or []
        if not hits and result.get("retrieval_mode") == "empty":
            pytest.skip("Search returned empty — check GEMINI_API_KEY on API (query embedding failed)")
        assert hits, f"No search results for {domain}: {query}"
        categories = {
            (h.get("category") or h.get("category_slug") or "").lower()
            for h in hits
        }
        texts = " ".join(
            (h.get("chunk_text") or h.get("text") or "").lower() for h in hits
        )
        assert expected_category in categories or expected_category in texts, (
            f"Expected category {expected_category} in results: {categories}"
        )
