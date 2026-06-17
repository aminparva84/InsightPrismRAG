# PrismRAG — development and QA helpers
# Usage: make <target>

.PHONY: help install run test qa deploy seed-qa quality-report

BASE_URL ?= http://localhost:8001
TAG      ?= latest

help:
	@echo ""
	@echo "  PrismRAG — available targets"
	@echo ""
	@echo "  Development"
	@echo "    make install        Install Python dependencies"
	@echo "    make run            Run API locally on port 8001"
	@echo ""
	@echo "  Testing"
	@echo "    make seed-qa        Seed QA domain data into Postgres"
	@echo "    make test           Run full test suite (local)"
	@echo "    make qa BASE_URL=https://api.prismrag.io   Run QA against deployed API"
	@echo "    make quality-report View latest quality report"
	@echo ""
	@echo "  Deployment"
	@echo "    make deploy TAG=v1.0.0   Build, push, and deploy to Azure"
	@echo ""

install:
	pip install -r requirements.txt -r requirements-test.txt

run:
	uvicorn main:app --host 0.0.0.0 --port 8001 --reload

seed-qa:
	python tests/seed_qa_data.py

seed-qa-drop:
	python tests/seed_qa_data.py --drop

# Run tests against local instance
test:
	pytest tests/ --base-url=$(BASE_URL) -v --tb=short -x

# Full QA suite against any deployed instance
qa:
	pytest tests/ --base-url=$(BASE_URL) -v --tb=short \
	  --junit-xml=tests/results.xml \
	  -p no:warnings

# Quality-only tests with verbose output
quality:
	pytest tests/test_quality.py --base-url=$(BASE_URL) -v -s

quality-report:
	@python -c "\
import json, sys; \
data = json.load(open('tests/quality_report.json')); \
s = data['summary']; \
print('\n=== PrismRAG Quality Report ==='); \
print(f\"Generated: {data['generated_at']}\"); \
print(f\"Search P@1 avg:              {s.get('avg_search_precision_at_1')}\"); \
print(f\"Deliberation relevance avg:  {s.get('avg_deliberation_domain_relevance')}\"); \
print('\nSearch by domain:'); \
[print(f\"  {k:12s}: P@1={v.get('precision_at_1')}  P@3={v.get('precision_at_3')}  spread={v.get('avg_score_spread')}\") for k,v in data['search'].items()]; \
print('\nDeliberation by case:'); \
[print(f\"  {k:28s}: relevance={v.get('domain_relevance')}  completeness={v.get('completeness')}  conf={v.get('synthesis_confidence')}  {v.get('elapsed_s')}s\") for k,v in data['deliberation'].items()]; \
print() \
"

deploy:
	chmod +x infra/deploy.sh
	./infra/deploy.sh $(TAG)
