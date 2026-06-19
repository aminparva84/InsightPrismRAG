"""
Post-deploy smoke test — runs in ~30-60s, requires no seed data.
Usage:
    BASE_URL=https://api.prismrag.insightits.com pytest tests/test_smoke.py -v
"""
import os
import time
import uuid

import pytest
import httpx

BASE_URL = os.getenv("BASE_URL", "http://localhost:8001").rstrip("/")
SMOKE_EMAIL = os.getenv("SMOKE_EMAIL", f"smoke-{uuid.uuid4().hex[:8]}@prismrag-test.invalid")
SMOKE_PASS  = os.getenv("SMOKE_PASS",  "SmokeTest!2025#")
TIMEOUT     = 20  # seconds per request


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as c:
        yield c


@pytest.fixture(scope="module")
def auth_token(client):
    """Register a throw-away account and return a JWT."""
    reg = client.post("/api/v1/auth/register", json={"email": SMOKE_EMAIL, "password": SMOKE_PASS})
    if reg.status_code == 409:
        # Account already exists from a previous smoke run — just log in
        login = client.post("/api/v1/auth/login", json={"email": SMOKE_EMAIL, "password": SMOKE_PASS})
        assert login.status_code == 200, f"Login failed: {login.text}"
        return login.json()["token"]

    assert reg.status_code in (200, 201), f"Register failed ({reg.status_code}): {reg.text}"
    login = client.post("/api/v1/auth/login", json={"email": SMOKE_EMAIL, "password": SMOKE_PASS})
    assert login.status_code == 200, f"Login after register failed: {login.text}"
    return login.json()["token"]


# ── Health ────────────────────────────────────────────────────────────────────

def test_health(client):
    """Service is up and returning 200."""
    r = client.get("/api/v1/prismrag/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") in ("ok", "healthy", "up"), f"Unexpected health body: {body}"


def test_docs_reachable(client):
    """Swagger docs render (not 5xx)."""
    r = client.get("/docs")
    assert r.status_code == 200


def test_openapi_json(client):
    """OpenAPI schema must generate — Swagger UI depends on /openapi.json."""
    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text[:500]
    body = r.json()
    assert "openapi" in body
    assert len(body.get("paths", {})) > 0


def test_metrics_endpoint(client):
    """Prometheus /metrics endpoint exists."""
    r = client.get("/metrics")
    assert r.status_code == 200


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_login_returns_jwt(client, auth_token):
    """Login flow produces a non-empty JWT."""
    assert auth_token and len(auth_token) > 20


def test_me_endpoint(client, auth_token):
    """/api/v1/auth/me returns user email."""
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {auth_token}"})
    assert r.status_code == 200
    assert r.json().get("email") == SMOKE_EMAIL


def test_unauthorized_returns_401(client):
    """Protected endpoint rejects missing token."""
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


# ── Tenant ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def smoke_tenant(client, auth_token):
    """Return an existing tenant or create one.  Free-plan accounts get 1 workspace max."""
    headers = {"Authorization": f"Bearer {auth_token}"}
    # Try to use existing tenant first (avoids free-plan quota errors on repeat runs)
    list_r = client.get("/api/v1/prismrag/tenants", headers=headers)
    if list_r.status_code == 200:
        tenants = list_r.json()
        if tenants:
            tenant_id = tenants[0].get("tenant_id") or tenants[0].get("id")
            if tenant_id:
                return tenant_id
    # No existing tenant — create one
    r = client.post("/api/v1/prismrag/tenants", json={"name": "smoke-tenant"}, headers=headers)
    assert r.status_code in (200, 201), f"Tenant create failed: {r.text}"
    tenant_id = r.json().get("tenant_id") or r.json().get("id")
    assert tenant_id
    return tenant_id


def test_tenant_list(client, auth_token, smoke_tenant):
    """Tenant appears in the tenants list."""
    r = client.get("/api/v1/prismrag/tenants", headers={"Authorization": f"Bearer {auth_token}"})
    assert r.status_code == 200
    ids = [t.get("tenant_id") or t.get("id") for t in r.json()]
    assert smoke_tenant in ids


# ── RAG search (empty corpus — expect 200 + empty results, not 500) ───────────

def test_search_empty_corpus(client, auth_token, smoke_tenant):
    """Search on an empty tenant returns 200 (or 402/403 on free plan without graph_rag)."""
    headers = {"Authorization": f"Bearer {auth_token}"}
    r = client.post(
        "/api/v1/prismrag/search",
        json={"query": "what is the refund policy?", "tenant_id": smoke_tenant, "top_k": 3},
        headers=headers,
    )
    assert r.status_code in (200, 402, 403), f"Unexpected search response: {r.status_code} {r.text}"
    if r.status_code == 200:
        body = r.json()
        assert isinstance(body.get("results"), list)


# ── Dashboard ─────────────────────────────────────────────────────────────────

def test_dashboard_usage(client, auth_token):
    """/api/v1/dashboard/usage returns a valid usage object."""
    r = client.get("/api/v1/dashboard/usage", headers={"Authorization": f"Bearer {auth_token}"})
    assert r.status_code == 200
    body = r.json()
    assert "usage" in body
    assert "limits" in body
    assert "remaining" in body


# ── Billing ───────────────────────────────────────────────────────────────────

def test_billing_plans(client, auth_token):
    """/api/v1/billing/plans returns a list of plans."""
    r = client.get("/api/v1/billing/plans", headers={"Authorization": f"Bearer {auth_token}"})
    assert r.status_code == 200
    plans = r.json().get("plans") or r.json()
    assert isinstance(plans, list) and len(plans) >= 1


# ── Status page ───────────────────────────────────────────────────────────────

def test_status_api(client):
    """/api/v1/status returns a components list."""
    r = client.get("/api/v1/status")
    assert r.status_code == 200
    body = r.json()
    # Accept any shape that includes a components/services key
    assert any(k in body for k in ("components", "services", "status")), f"Unexpected status body: {body}"


# ── Deliberation smoke (does not call Gemini; just validates routing) ─────────

def test_deliberation_endpoint_exists(client, auth_token, smoke_tenant):
    """Deliberation endpoint accepts the request shape (may return 422/402 without corpus)."""
    headers = {"Authorization": f"Bearer {auth_token}"}
    r = client.post(
        "/api/v1/deliberation/run",
        json={"query": "What are the main risks?", "tenant_id": smoke_tenant},
        headers=headers,
    )
    # 200 = success, 402 = no plan, 422 = validation, 503 = Gemini unavailable
    # Any of these prove the route exists and authentication works
    assert r.status_code != 404, "Deliberation route missing"
    assert r.status_code != 500, f"Deliberation 500: {r.text}"
