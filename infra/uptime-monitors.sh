#!/usr/bin/env bash
# PrismRAG — Uptime monitoring setup via BetterStack API
# Usage:
#   export BETTERSTACK_API_TOKEN=<your_token>
#   export API_URL=https://api.prismrag.insightits.com
#   bash infra/uptime-monitors.sh
#
# Requires: curl, jq
# BetterStack docs: https://betterstack.com/docs/uptime/api/getting-started-with-uptime-api/

set -euo pipefail

BETTERSTACK_API_TOKEN="${BETTERSTACK_API_TOKEN:?Set BETTERSTACK_API_TOKEN}"
API_URL="${API_URL:-https://api.prismrag.insightits.com}"
STATUS_PAGE_SUBDOMAIN="${STATUS_PAGE_SUBDOMAIN:-prismrag}"

BS="https://uptime.betterstack.com/api/v2"
AUTH="Authorization: Bearer ${BETTERSTACK_API_TOKEN}"

echo "Setting up BetterStack monitors for ${API_URL} ..."

create_monitor() {
  local name="$1" url="$2" check_freq="${3:-60}" regions="${4:-us,eu,ap}"
  curl -sf -X POST "${BS}/monitors" \
    -H "${AUTH}" \
    -H "Content-Type: application/json" \
    -d "{
      \"monitor_type\": \"status\",
      \"url\": \"${url}\",
      \"friendly_name\": \"${name}\",
      \"check_frequency\": ${check_freq},
      \"request_timeout\": 15,
      \"expected_status_codes\": [200],
      \"regions\": [\"${regions//,/\",\"}\"],
      \"email\": true
    }" | jq -r '.data.id'
}

# API health
API_MONITOR_ID=$(create_monitor \
  "PrismRAG API" \
  "${API_URL}/api/v1/prismrag/health" \
  60 \
  "us,eu,ap")
echo "  Created API monitor: ${API_MONITOR_ID}"

# Docs endpoint
DOCS_MONITOR_ID=$(create_monitor \
  "PrismRAG Docs" \
  "${API_URL}/docs" \
  300 \
  "us,eu")
echo "  Created Docs monitor: ${DOCS_MONITOR_ID}"

# Web frontend (if hosted)
if [[ -n "${WEB_URL:-}" ]]; then
  WEB_MONITOR_ID=$(create_monitor \
    "PrismRAG Web" \
    "${WEB_URL}" \
    120 \
    "us,eu")
  echo "  Created Web monitor: ${WEB_MONITOR_ID}"
fi

echo ""
echo "Creating status page ..."
STATUS_PAGE_ID=$(curl -sf -X POST "${BS}/status-pages" \
  -H "${AUTH}" \
  -H "Content-Type: application/json" \
  -d "{
    \"company_name\": \"PrismRAG\",
    \"company_url\": \"https://prismrag.insightits.com\",
    \"subdomain\": \"${STATUS_PAGE_SUBDOMAIN}\",
    \"custom_domain\": \"status.prismrag.insightits.com\",
    \"timezone\": \"UTC\",
    \"subscribe_enabled\": true
  }" | jq -r '.data.id')
echo "  Status page ID: ${STATUS_PAGE_ID}"

# Add monitors to status page
for mid in "${API_MONITOR_ID}" "${DOCS_MONITOR_ID}"; do
  curl -sf -X POST "${BS}/status-pages/${STATUS_PAGE_ID}/resources" \
    -H "${AUTH}" \
    -H "Content-Type: application/json" \
    -d "{
      \"monitor_id\": \"${mid}\",
      \"public_name\": \"API\"
    }" > /dev/null
done
echo "  Monitors added to status page."

echo ""
echo "Done."
echo "  Public status page: https://${STATUS_PAGE_SUBDOMAIN}.betteruptime.com"
echo "  Add CNAME: status.prismrag.insightits.com → ${STATUS_PAGE_SUBDOMAIN}.betteruptime.com"
