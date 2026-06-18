"""
PrismRAG QA test suite — shared fixtures.

Usage:
  pytest tests/ --base-url=https://api.prismrag.io -v
  pytest tests/ --base-url=http://localhost:8001 -v       # local
  pytest tests/ --base-url=http://localhost:8001 --seeded -v  # use pre-seeded tenant IDs

Env vars (override --base-url):
  PRISMRAG_TEST_URL      — API base URL
  PRISMRAG_TEST_EMAIL    — test user email (auto-created / or qa-local@test.prismrag.io when seeded)
  PRISMRAG_TEST_PASSWORD — test user password
  QA_SEEDED              — set to "1" to use fixed seeded tenant IDs (same as --seeded flag)

Pre-seeded mode (--seeded / QA_SEEDED=1):
  Uses fixed UUIDs planted by `python tests/seed_qa_data.py`. Tenant creation is
  skipped — tests run against the already-seeded data in the local DB, which is
  faster and avoids API-rate issues.
"""
import os
import time
import uuid

import pytest
import requests

from tests.helpers import login as _login

# ── Config ─────────────────────────────────────────────────────────────────────

def pytest_addoption(parser):
    parser.addoption("--base-url", default="http://localhost:8001",
                     help="API base URL (default: http://localhost:8001)")
    parser.addoption("--seeded", action="store_true", default=False,
                     help="Use pre-seeded fixed tenant IDs from seed_qa_data.py")

# Fixed UUIDs planted by seed_qa_data.py
QA_SEEDED_TENANT_IDS = {
    "healthcare": "10000000-0000-0000-0000-000000000001",
    "pharmacy":   "10000000-0000-0000-0000-000000000002",
    "finance":    "10000000-0000-0000-0000-000000000003",
}
QA_SEEDED_EMAIL    = "qa-local@test.prismrag.io"
QA_SEEDED_PASSWORD = "QaTestPass!123"


@pytest.fixture(scope="session")
def base_url(request):
    env = os.environ.get("PRISMRAG_TEST_URL", "")
    return env.rstrip("/") if env else request.config.getoption("--base-url").rstrip("/")


@pytest.fixture(scope="session")
def use_seeded(request):
    """True when running against pre-seeded local DB (fixed tenant IDs)."""
    return (
        request.config.getoption("--seeded")
        or os.environ.get("QA_SEEDED", "").strip() == "1"
    )

@pytest.fixture(scope="session")
def api(base_url):
    """Requests session with base URL helper."""
    s = requests.Session()
    s.headers["Content-Type"] = "application/json"

    def _url(path):
        return f"{base_url}{path}"

    s.url = _url
    return s

# ── Auth fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qa_credentials(use_seeded):
    if use_seeded:
        return {
            "email":    os.environ.get("PRISMRAG_TEST_EMAIL", QA_SEEDED_EMAIL),
            "password": os.environ.get("PRISMRAG_TEST_PASSWORD", QA_SEEDED_PASSWORD),
            "name":     "QA Local User",
        }
    suffix = uuid.uuid4().hex[:8]
    return {
        "email":    os.environ.get("PRISMRAG_TEST_EMAIL", f"qa-{suffix}@test.prismrag.io"),
        "password": os.environ.get("PRISMRAG_TEST_PASSWORD", f"QaPass!{suffix}"),
        "name":     f"QA User {suffix}",
    }

@pytest.fixture(scope="session")
def auth_token(api, qa_credentials):
    """Register and return a valid JWT. Cached for the full session."""
    r = api.post(api.url("/api/v1/auth/register"), json={
        "email":     qa_credentials["email"],
        "password":  qa_credentials["password"],
        "full_name": qa_credentials["name"],
    })
    if r.status_code == 409:
        pass  # user already exists — proceed to login
    elif r.status_code not in (200, 201):
        pytest.fail(f"Register failed: {r.status_code} {r.text}")

    data = _login(api, qa_credentials["email"], qa_credentials["password"])
    token = data["token"]
    api.headers["Authorization"] = f"Bearer {token}"
    return token

@pytest.fixture(scope="session")
def authed_api(api, auth_token):
    """Requests session pre-loaded with Bearer token."""
    return api

# ── Domain tenant fixtures ────────────────────────────────────────────────────

# ── API path prefixes (matches actual router definitions) ─────────────────────
AUTH_API    = "/api/v1/auth"
RAG_API     = "/api/v1/prismrag"
DELIB_API   = "/api/v1/deliberation"
BILLING_API = "/api/v1/billing"
STATUS_API  = "/api/v1/status"
TENANT_API  = "/api/v1/tenants"


@pytest.fixture(scope="session")
def stripe_configured():
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key or key.startswith("PASTE_"):
        pytest.skip("STRIPE_SECRET_KEY not configured in .env")
    return True


@pytest.fixture(scope="session")
def healthcare_tenant(authed_api, use_seeded):
    if use_seeded:
        return QA_SEEDED_TENANT_IDS["healthcare"]
    r = authed_api.post(authed_api.url(f"{RAG_API}/tenants"), json={
        "name": "QA Healthcare Clinic",
    })
    assert r.status_code in (200, 201), f"Create tenant failed: {r.text}"
    return r.json()["tenant_id"]

@pytest.fixture(scope="session")
def pharmacy_tenant(authed_api, use_seeded):
    if use_seeded:
        return QA_SEEDED_TENANT_IDS["pharmacy"]
    r = authed_api.post(authed_api.url(f"{RAG_API}/tenants"), json={
        "name": "QA PharmaCo",
    })
    assert r.status_code in (200, 201), f"Create tenant failed: {r.text}"
    return r.json()["tenant_id"]

@pytest.fixture(scope="session")
def finance_tenant(authed_api, use_seeded):
    if use_seeded:
        return QA_SEEDED_TENANT_IDS["finance"]
    r = authed_api.post(authed_api.url(f"{RAG_API}/tenants"), json={
        "name": "QA FinanceCo",
    })
    assert r.status_code in (200, 201), f"Create tenant failed: {r.text}"
    return r.json()["tenant_id"]

# ── Shared mapping payloads ───────────────────────────────────────────────────

HEALTHCARE_MAPPING = {
    "categories": [
        {"slug": "diagnosis",      "label": "Diagnosis & Classification"},
        {"slug": "symptoms",       "label": "Symptoms & Clinical Signs"},
        {"slug": "treatment",      "label": "Treatment & Therapy"},
        {"slug": "medication",     "label": "Medication & Pharmacotherapy"},
        {"slug": "procedures",     "label": "Clinical Procedures"},
        {"slug": "lab_results",    "label": "Laboratory Results"},
        {"slug": "patient_safety", "label": "Patient Safety & Risk"},
    ],
    "rules": [
        {"word": "hypertension",        "category_slug": "diagnosis"},
        {"word": "diabetes",            "category_slug": "diagnosis"},
        {"word": "sepsis",              "category_slug": "diagnosis"},
        {"word": "chest_pain",          "category_slug": "symptoms"},
        {"word": "tachycardia",         "category_slug": "symptoms"},
        {"word": "fever",               "category_slug": "symptoms"},
        {"word": "antibiotics",         "category_slug": "treatment"},
        {"word": "oxygen_therapy",      "category_slug": "treatment"},
        {"word": "metformin",           "category_slug": "medication"},
        {"word": "warfarin",            "category_slug": "medication"},
        {"word": "insulin",             "category_slug": "medication"},
        {"word": "ecg",                 "category_slug": "procedures"},
        {"word": "mri",                 "category_slug": "procedures"},
        {"word": "hba1c",               "category_slug": "lab_results"},
        {"word": "troponin",            "category_slug": "lab_results"},
        {"word": "creatinine",          "category_slug": "lab_results"},
        {"word": "drug_allergy",        "category_slug": "patient_safety"},
        {"word": "fall_risk",           "category_slug": "patient_safety"},
        {"word": "anaphylaxis",         "category_slug": "patient_safety"},
    ],
}

PHARMACY_MAPPING = {
    "categories": [
        {"slug": "drug_interactions", "label": "Drug Interactions"},
        {"slug": "dosage",            "label": "Dosing & Administration"},
        {"slug": "contraindications", "label": "Contraindications"},
        {"slug": "adverse_effects",   "label": "Adverse Effects"},
        {"slug": "pharmacokinetics",  "label": "Pharmacokinetics"},
        {"slug": "mechanisms",        "label": "Mechanism of Action"},
        {"slug": "storage",           "label": "Storage & Stability"},
    ],
    "rules": [
        {"word": "cyp450",             "category_slug": "drug_interactions"},
        {"word": "cyp3a4",             "category_slug": "drug_interactions"},
        {"word": "polypharmacy",       "category_slug": "drug_interactions"},
        {"word": "loading_dose",       "category_slug": "dosage"},
        {"word": "maintenance_dose",   "category_slug": "dosage"},
        {"word": "renal_dose_adjustment","category_slug": "dosage"},
        {"word": "contraindicated",    "category_slug": "contraindications"},
        {"word": "black_box_warning",  "category_slug": "contraindications"},
        {"word": "hepatotoxicity",     "category_slug": "adverse_effects"},
        {"word": "qt_prolongation",    "category_slug": "adverse_effects"},
        {"word": "serotonin_syndrome", "category_slug": "adverse_effects"},
        {"word": "half_life",          "category_slug": "pharmacokinetics"},
        {"word": "bioavailability",    "category_slug": "pharmacokinetics"},
        {"word": "protein_binding",    "category_slug": "pharmacokinetics"},
        {"word": "beta_blocker",       "category_slug": "mechanisms"},
        {"word": "ace_inhibitor",      "category_slug": "mechanisms"},
        {"word": "ssri",               "category_slug": "mechanisms"},
        {"word": "refrigerate",        "category_slug": "storage"},
        {"word": "cold_chain",         "category_slug": "storage"},
    ],
}

FINANCE_MAPPING = {
    "categories": [
        {"slug": "risk",            "label": "Risk & Compliance"},
        {"slug": "growth",          "label": "Growth & Opportunity"},
        {"slug": "valuation",       "label": "Valuation & Pricing"},
        {"slug": "liquidity",       "label": "Liquidity & Cash Flow"},
        {"slug": "debt",            "label": "Debt & Capital Structure"},
        {"slug": "market_analysis", "label": "Market Analysis"},
        {"slug": "regulatory",      "label": "Regulatory & Reporting"},
    ],
    "rules": [
        {"word": "volatility",       "category_slug": "risk"},
        {"word": "var",              "category_slug": "risk"},
        {"word": "credit_risk",      "category_slug": "risk"},
        {"word": "beta",             "category_slug": "risk"},
        {"word": "alpha",            "category_slug": "growth"},
        {"word": "revenue_growth",   "category_slug": "growth"},
        {"word": "cagr",             "category_slug": "growth"},
        {"word": "dcf",              "category_slug": "valuation"},
        {"word": "ebitda",           "category_slug": "valuation"},
        {"word": "wacc",             "category_slug": "valuation"},
        {"word": "free_cash_flow",   "category_slug": "liquidity"},
        {"word": "current_ratio",    "category_slug": "liquidity"},
        {"word": "burn_rate",        "category_slug": "liquidity"},
        {"word": "debt_to_equity",   "category_slug": "debt"},
        {"word": "covenant",         "category_slug": "debt"},
        {"word": "credit_rating",    "category_slug": "debt"},
        {"word": "total_addressable_market","category_slug": "market_analysis"},
        {"word": "competitive_moat", "category_slug": "market_analysis"},
        {"word": "sec_filing",       "category_slug": "regulatory"},
        {"word": "sox_compliance",   "category_slug": "regulatory"},
    ],
}

DOMAIN_CONFIGS = {
    "healthcare": {"mapping": HEALTHCARE_MAPPING, "search_queries": [
        ("What medications are used for diabetes management?", "medication"),
        ("What lab tests diagnose heart attack?", "lab_results"),
        ("What are the patient safety risks for drug allergy?", "patient_safety"),
    ]},
    "pharmacy": {"mapping": PHARMACY_MAPPING, "search_queries": [
        ("What are the CYP450 drug interactions for warfarin?", "drug_interactions"),
        ("How should insulin be stored?", "storage"),
        ("What is the bioavailability and half-life of amoxicillin?", "pharmacokinetics"),
    ]},
    "finance": {"mapping": FINANCE_MAPPING, "search_queries": [
        ("What is the DCF valuation and WACC analysis?", "valuation"),
        ("What are the credit risk and VaR metrics?", "risk"),
        ("What is the free cash flow and current ratio?", "liquidity"),
    ]},
}
