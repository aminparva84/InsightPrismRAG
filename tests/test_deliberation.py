"""Tests — Deliberation API across 3 domain scenarios."""
import time
import pytest
from tests.conftest import DELIB_API


DELIBERATION_QUESTIONS = [
    {
        "id": "healthcare-ma",
        "question": "What are the clinical risks and treatment protocols when a patient with diabetes presents with chest pain and elevated troponin?",
        "expected_domains_any_of": ["cardiology", "endocrinology", "internal medicine", "emergency", "diagnosis", "medication", "treatment"],
        "tenant": "healthcare",
        "should_conflict": True,
    },
    {
        "id": "pharmacy-interaction",
        "question": "What drug interactions and dose adjustments should be considered for a patient on warfarin who requires a new SSRI prescription?",
        "expected_domains_any_of": ["pharmacology", "drug interactions", "psychiatry", "dosage", "adverse effects"],
        "tenant": "pharmacy",
        "should_conflict": True,
    },
    {
        "id": "finance-acquisition",
        "question": "What are the valuation, risk, and regulatory considerations for acquiring a fintech startup with high growth but negative cash flow?",
        "expected_domains_any_of": ["finance", "valuation", "risk", "regulatory", "market", "legal", "liquidity"],
        "tenant": "finance",
        "should_conflict": True,
    },
]


def _tenant(tc, healthcare_tenant, pharmacy_tenant, finance_tenant):
    return {"healthcare": healthcare_tenant,
            "pharmacy":   pharmacy_tenant,
            "finance":    finance_tenant}.get(tc.get("tenant"))


class TestDeliberationCreate:

    def test_health(self, authed_api):
        r = authed_api.get(authed_api.url(f"{DELIB_API}/health"))
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_question_too_short(self, authed_api):
        r = authed_api.post(authed_api.url(f"{DELIB_API}/sessions"), json={"question": "hi"})
        assert r.status_code == 422

    def test_domain_count_out_of_range(self, authed_api):
        for bad in [2, 11]:
            r = authed_api.post(authed_api.url(f"{DELIB_API}/sessions"), json={
                "question":     "What are the key financial risks in a cross-border acquisition?",
                "domain_count": bad,
            })
            assert r.status_code == 422, f"domain_count={bad} should be rejected"

    @pytest.mark.parametrize("tc", DELIBERATION_QUESTIONS, ids=[t["id"] for t in DELIBERATION_QUESTIONS])
    def test_sync_deliberation(self, tc, authed_api, healthcare_tenant, pharmacy_tenant, finance_tenant):
        payload = {
            "question":     tc["question"],
            "domain_count": 7,
            "async_mode":   False,
        }
        tid = _tenant(tc, healthcare_tenant, pharmacy_tenant, finance_tenant)
        if tid:
            payload["tenant_id"] = tid

        start = time.time()
        r = authed_api.post(authed_api.url(f"{DELIB_API}/sessions"), json=payload, timeout=120)
        elapsed = time.time() - start
        assert r.status_code in (200, 201, 202), f"Deliberation failed: {r.status_code} {r.text[:400]}"
        data = r.json()
        assert data["status"] == "done"

        # Domains
        domains = data.get("domains", [])
        assert len(domains) >= 3
        found = any(exp.lower() in " ".join(d["name"].lower() for d in domains)
                    for exp in tc["expected_domains_any_of"])
        assert found, f"None of {tc['expected_domains_any_of']} in {[d['name'] for d in domains]}"

        # Verticals
        verticals = data.get("verticals", [])
        assert len(verticals) >= 3
        for v in verticals:
            assert v.get("findings"), f"Empty findings for domain {v.get('domain')}"

        # Synthesis
        synth = data.get("synthesis", {})
        assert synth.get("final_answer") and len(synth["final_answer"]) > 50
        assert synth.get("agreements")
        if tc["should_conflict"]:
            assert len(synth.get("conflicts", "")) > 20, "Expected conflicts for complex question"
        assert synth.get("unique_insights")

        assert elapsed < 90, f"Deliberation took {elapsed:.1f}s — exceeds 90s threshold"
        print(f"\n[{tc['id']}] {elapsed:.1f}s | conf={synth.get('confidence', 0):.2f} "
              f"| domains={[d['name'] for d in domains]}")


class TestDeliberationAsync:

    def test_async_returns_immediately(self, authed_api):
        start = time.time()
        r = authed_api.post(authed_api.url(f"{DELIB_API}/sessions"), json={
            "question":   "What are the regulatory risks of launching an AI-powered medical device in the EU?",
            "async_mode": True,
        }, timeout=10)
        assert r.status_code in (200, 201, 202)
        assert r.json().get("async") is True
        assert time.time() - start < 5.0

    def test_poll_to_completion(self, authed_api):
        r = authed_api.post(authed_api.url(f"{DELIB_API}/sessions"), json={
            "question":   "What is the impact of rising interest rates on fintech lending models?",
            "async_mode": True,
        })
        sid = r.json()["session_id"]
        for _ in range(30):
            pr = authed_api.get(authed_api.url(f"{DELIB_API}/sessions/{sid}"))
            status = pr.json()["status"]
            if status == "done":
                assert pr.json()["synthesis"] is not None
                return
            if status == "failed":
                pytest.fail(f"Session {sid} failed")
            time.sleep(5)
        pytest.fail(f"Session {sid} never completed in 150s")


class TestDeliberationSession:

    @pytest.fixture(scope="class")
    def session(self, authed_api, finance_tenant):
        r = authed_api.post(authed_api.url(f"{DELIB_API}/sessions"), json={
            "question":   "What should we consider before a cross-border M&A acquisition in Southeast Asia?",
            "tenant_id":  finance_tenant,
            "async_mode": False,
        }, timeout=120)
        assert r.status_code in (200, 201, 202)
        return r.json()

    def test_get_by_id(self, authed_api, session):
        r = authed_api.get(authed_api.url(f"{DELIB_API}/sessions/{session['session_id']}"))
        assert r.status_code == 200
        assert r.json()["session_id"] == session["session_id"]

    def test_get_domains(self, authed_api, session):
        r = authed_api.get(authed_api.url(f"{DELIB_API}/sessions/{session['session_id']}/domains"))
        assert r.status_code == 200
        assert len(r.json()["domains"]) >= 3

    def test_followup(self, authed_api, session):
        r = authed_api.post(
            authed_api.url(f"{DELIB_API}/sessions/{session['session_id']}/followup"),
            json={"question": "Which risk should we address first?"},
        )
        assert r.status_code == 200
        assert len(r.json().get("answer", "")) > 50

    def test_followup_is_free(self, authed_api, session):
        """Multiple follow-ups on same session don't consume quota."""
        for _ in range(3):
            r = authed_api.post(
                authed_api.url(f"{DELIB_API}/sessions/{session['session_id']}/followup"),
                json={"question": "Can you elaborate on the regulatory risks?"},
            )
            assert r.status_code == 200

    def test_list_sessions(self, authed_api, session):
        r = authed_api.get(authed_api.url(f"{DELIB_API}/sessions"))
        assert r.status_code == 200
        assert session["session_id"] in [s["session_id"] for s in r.json()]

    def test_session_not_found(self, authed_api):
        r = authed_api.get(authed_api.url(f"{DELIB_API}/sessions/00000000-0000-0000-0000-000000000000"))
        assert r.status_code == 404
