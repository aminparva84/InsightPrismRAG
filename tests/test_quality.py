"""
Tests — Result quality evaluation.

Measures quality metrics (not just pass/fail) and writes a JSON report to
tests/quality_report.json after each run.

Run standalone:
  pytest tests/test_quality.py -v -s --base-url=https://api.prismrag.io

Quality metrics
───────────────
Search
  precision@1      top result in correct category
  precision@3      ≥2 of top 3 in correct category
  score_spread     max_score - min_score (higher = better discrimination)

Deliberation
  domain_relevance      fraction of expected domains actually discovered
  completeness          all 4 synthesis fields non-empty and substantive
  conf_calibrated       synthesis.confidence ∈ [0.4, 0.97]
  conflict_present      conflicts field present and >30 chars (for complex Qs)
  latency_s             wall-clock seconds for full sync deliberation
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.conftest import RAG_API, DELIB_API, DOMAIN_CONFIGS, HEALTHCARE_MAPPING, PHARMACY_MAPPING, FINANCE_MAPPING
from tests.test_prismrag import ingest_job

REPORT_PATH = Path("tests/quality_report.json")

_report: dict = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "search":        {},
    "deliberation":  {},
    "summary":       {},
}


# ── Module-scoped job fixtures ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def all_jobs(authed_api, healthcare_tenant, pharmacy_tenant, finance_tenant):
    return {
        "healthcare": ingest_job(authed_api, healthcare_tenant, HEALTHCARE_MAPPING),
        "pharmacy":   ingest_job(authed_api, pharmacy_tenant,   PHARMACY_MAPPING),
        "finance":    ingest_job(authed_api, finance_tenant,    FINANCE_MAPPING),
    }


# ── Search quality ────────────────────────────────────────────────────────────

class TestSearchQuality:

    @pytest.mark.parametrize("domain", ["healthcare", "pharmacy", "finance"])
    def test_category_precision(
        self, domain, authed_api,
        healthcare_tenant, pharmacy_tenant, finance_tenant, all_jobs,
    ):
        tenant_id = {"healthcare": healthcare_tenant,
                     "pharmacy":   pharmacy_tenant,
                     "finance":    finance_tenant}[domain]
        test_cases = DOMAIN_CONFIGS[domain]["search_queries"]

        p1_scores, p3_scores, spreads = [], [], []

        for query, expected_cat in test_cases:
            r = authed_api.post(authed_api.url(f"{RAG_API}/search"), json={
                "tenant_id": tenant_id, "query": query, "top_k": 5,
            })
            assert r.status_code == 200
            results = r.json().get("results", [])
            if not results:
                p1_scores.append(0); p3_scores.append(0); continue

            cats   = [res["category_slug"] for res in results]
            scores = [res["score"] for res in results]
            p1_scores.append(1 if cats[0] == expected_cat else 0)
            p3_scores.append(sum(1 for c in cats[:3] if c == expected_cat) / min(3, len(cats)))
            spreads.append(max(scores) - min(scores) if len(scores) > 1 else 0.0)

        avg_p1     = sum(p1_scores) / len(p1_scores)
        avg_p3     = sum(p3_scores) / len(p3_scores)
        avg_spread = sum(spreads) / len(spreads) if spreads else 0.0

        _report["search"][domain] = {
            "precision_at_1":   round(avg_p1, 3),
            "precision_at_3":   round(avg_p3, 3),
            "avg_score_spread": round(avg_spread, 4),
            "queries_tested":   len(test_cases),
        }
        print(f"\n[SEARCH QUALITY:{domain}]  P@1={avg_p1:.2f}  P@3={avg_p3:.2f}  spread={avg_spread:.3f}")

        # Minimum threshold: P@1 ≥ 0.50
        assert avg_p1 >= 0.50, (
            f"Search precision@1 for {domain}={avg_p1:.2f} < 0.50. "
            "Ensure ingestion completed and community detection ran."
        )


# ── Deliberation quality ──────────────────────────────────────────────────────

DELIB_CASES = [
    {
        "id": "healthcare-complexity",
        "question": "A diabetic patient is admitted with sepsis and acute kidney injury. What are the clinical, medication, and safety considerations?",
        "expected_domains": ["medication", "treatment", "diagnosis", "patient safety", "nephrology", "endocrinology"],
        "min_domains": 3,
        "expect_conflict": True,
        "tenant": "healthcare",
    },
    {
        "id": "finance-complexity",
        "question": "Should we issue equity or take on debt to fund our international expansion, given current market volatility and regulatory requirements?",
        "expected_domains": ["finance", "risk", "regulatory", "market", "valuation", "debt"],
        "min_domains": 4,
        "expect_conflict": True,
        "tenant": "finance",
    },
    {
        "id": "pharmacy-complexity",
        "question": "What are the pharmacokinetic interactions and adverse effects when adding an SSRI to a patient already on warfarin and a statin?",
        "expected_domains": ["pharmacokinetics", "drug interactions", "adverse effects", "dosage"],
        "min_domains": 3,
        "expect_conflict": True,
        "tenant": "pharmacy",
    },
]


class TestDeliberationQuality:

    @pytest.mark.parametrize("tc", DELIB_CASES, ids=[t["id"] for t in DELIB_CASES])
    def test_deliberation_quality(
        self, tc, authed_api,
        healthcare_tenant, pharmacy_tenant, finance_tenant,
    ):
        tenant_id = {"healthcare": healthcare_tenant,
                     "pharmacy":   pharmacy_tenant,
                     "finance":    finance_tenant}.get(tc["tenant"])

        payload = {"question": tc["question"], "domain_count": 7, "async_mode": False}
        if tenant_id:
            payload["tenant_id"] = tenant_id

        start = time.time()
        r = authed_api.post(authed_api.url(f"{DELIB_API}/sessions"), json=payload, timeout=120)
        elapsed = time.time() - start

        assert r.status_code in (200, 201, 202)
        data = r.json()
        assert data["status"] == "done"

        domains   = data.get("domains", [])
        verticals = data.get("verticals", [])
        synth     = data.get("synthesis", {}) or {}

        # Domain relevance
        domain_text = " ".join(d["name"].lower() for d in domains)
        matched = sum(1 for exp in tc["expected_domains"] if exp.lower() in domain_text)
        domain_relevance = matched / len(tc["expected_domains"])

        # Synthesis completeness
        fields = {
            "agreements":      len(synth.get("agreements", "")) > 30,
            "conflicts":       len(synth.get("conflicts", "")) > (30 if tc["expect_conflict"] else 0),
            "unique_insights": len(synth.get("unique_insights", "")) > 20,
            "final_answer":    len(synth.get("final_answer", "")) > 80,
        }
        completeness = sum(fields.values()) / len(fields)
        conf = synth.get("confidence", 0.0)
        vert_confs = [v.get("confidence", 0.0) for v in verticals if v.get("confidence") is not None]

        quality = {
            "elapsed_s":             round(elapsed, 1),
            "domains_returned":      len(domains),
            "domain_relevance":      round(domain_relevance, 3),
            "completeness":          round(completeness, 3),
            "synthesis_confidence":  round(conf, 3),
            "conf_calibrated":       0.4 <= conf <= 0.97,
            "mean_vertical_conf":    round(sum(vert_confs) / len(vert_confs), 3) if vert_confs else 0,
            "conflict_present":      len(synth.get("conflicts", "")) > 30,
            "missing_fields":        [k for k, v in fields.items() if not v],
        }
        _report["deliberation"][tc["id"]] = quality

        print(f"\n[DELIB QUALITY:{tc['id']}]  elapsed={elapsed:.1f}s  "
              f"relevance={domain_relevance:.2f}  completeness={completeness:.2f}  conf={conf:.2f}")

        # Assertions with meaningful failure messages
        assert len(domains) >= tc["min_domains"], \
            f"Only {len(domains)} domains (expected >= {tc['min_domains']})"
        assert domain_relevance >= 0.3, \
            f"Domain relevance {domain_relevance:.2f} too low. Got: {[d['name'] for d in domains]}"
        assert completeness >= 0.75, \
            f"Synthesis completeness {completeness:.2f}. Missing: {quality['missing_fields']}"
        assert quality["conf_calibrated"], \
            f"Confidence {conf:.3f} outside [0.4, 0.97]"
        if tc["expect_conflict"]:
            assert quality["conflict_present"], \
                f"Expected conflicts for complex question — got: '{synth.get('conflicts', '')}'"


# ── Write report ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def write_quality_report(request):
    yield
    s_scores = [v["precision_at_1"] for v in _report["search"].values() if "precision_at_1" in v]
    d_scores = [v["domain_relevance"] for v in _report["deliberation"].values() if "domain_relevance" in v]
    _report["summary"] = {
        "avg_search_precision_at_1":         round(sum(s_scores) / len(s_scores), 3) if s_scores else None,
        "avg_deliberation_domain_relevance": round(sum(d_scores) / len(d_scores), 3) if d_scores else None,
        "domains_tested":                    list(_report["search"].keys()),
        "deliberation_cases":                list(_report["deliberation"].keys()),
    }
    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps(_report, indent=2))
    print(f"\n\nQuality report → {REPORT_PATH}")
    print(f"  Search P@1 avg:              {_report['summary']['avg_search_precision_at_1']}")
    print(f"  Deliberation relevance avg:  {_report['summary']['avg_deliberation_domain_relevance']}")
