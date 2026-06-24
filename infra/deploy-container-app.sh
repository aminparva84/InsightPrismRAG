#!/usr/bin/env bash
# Deploy or update a PrismRAG Container App with ACR pull via managed identity.
# Invoked from .github/workflows/deploy.yml — ONLY on [publish] commits or manual runs.
set -euo pipefail

: "${RESOURCE_GROUP:?}"
: "${APP_NAME:?}"
: "${IMAGE:?}"
: "${IDENTITY_ID:?}"
: "${ACR_LOGIN_SERVER:?}"
: "${CONTAINER_ENV:?}"

# Optional ingress (API only)
INGRESS="${INGRESS:-}"          # external | internal | empty
TARGET_PORT="${TARGET_PORT:-}"
MIN_REPLICAS="${MIN_REPLICAS:-1}"
MAX_REPLICAS="${MAX_REPLICAS:-10}"
CPU="${CPU:-0.5}"
MEMORY="${MEMORY:-1Gi}"

# Secrets (plain values — written to Container App secrets store)
: "${DB_DSN:?}"
: "${JWT_SECRET:?}"
: "${GEMINI_API_KEY:?}"
STRIPE_SK="${STRIPE_SK:-not-configured}"
STRIPE_WH="${STRIPE_WH:-not-configured}"
SB_CONN="${SB_CONN:-not-configured}"
ACS_CONN="${ACS_CONN:-not-configured}"
STORAGE_CONN="${STORAGE_CONN:-not-configured}"
NC="not-configured"

STRIPE_PRICE_STARTER="${STRIPE_PRICE_STARTER:-not-configured}"
STRIPE_PRICE_PROFESSIONAL="${STRIPE_PRICE_PROFESSIONAL:-not-configured}"
STRIPE_PRICE_ENTERPRISE="${STRIPE_PRICE_ENTERPRISE:-not-configured}"
PRISMRAG_EMAIL_FROM="${PRISMRAG_EMAIL_FROM:-PrismRAG@insightits.com}"
PRISMRAG_BASE_URL="${PRISMRAG_BASE_URL:-https://prismrag.insightits.com}"
PRISMRAG_ADMIN_EMAILS="${PRISMRAG_ADMIN_EMAILS:-prismrag@insightits.com}"
PRISMRAG_LOG_LEVEL="${PRISMRAG_LOG_LEVEL:-WARNING}"
PRISMRAG_LOG_BLOB_ENABLED="${PRISMRAG_LOG_BLOB_ENABLED:-true}"
PRISMRAG_LOG_BLOB_CONTAINER="${PRISMRAG_LOG_BLOB_CONTAINER:-prismrag-logs}"

echo "==> Deploy ${APP_NAME} (${IMAGE})"

SECRET_ARGS=(
  "db-dsn=${DB_DSN}"
  "jwt-secret=${JWT_SECRET}"
  "gemini-key=${GEMINI_API_KEY}"
  "stripe-secret=${STRIPE_SK}"
  "stripe-webhook=${STRIPE_WH}"
  "redis-url=${NC}"
  "servicebus-conn=${SB_CONN}"
  "acs-conn=${ACS_CONN}"
  "storage-conn=${STORAGE_CONN}"
)

ENV_ARGS=(
  "PRISMRAG_DB_DSN=secretref:db-dsn"
  "REDIS_URL=secretref:redis-url"
  "JWT_SECRET=secretref:jwt-secret"
  "GEMINI_API_KEY=secretref:gemini-key"
  "STRIPE_SECRET_KEY=secretref:stripe-secret"
  "STRIPE_WEBHOOK_SECRET=secretref:stripe-webhook"
  "AZURE_SERVICE_BUS_CONNECTION_STRING=secretref:servicebus-conn"
  "AZURE_COMMUNICATION_CONNECTION_STRING=secretref:acs-conn"
  "AZURE_STORAGE_CONNECTION_STRING=secretref:storage-conn"
  "STRIPE_PRICE_STARTER=${STRIPE_PRICE_STARTER}"
  "STRIPE_PRICE_PROFESSIONAL=${STRIPE_PRICE_PROFESSIONAL}"
  "STRIPE_PRICE_ENTERPRISE=${STRIPE_PRICE_ENTERPRISE}"
  "PRISMRAG_ENV=production"
  "PRISMRAG_EMAIL_ENABLED=true"
  "PRISMRAG_EMAIL_FROM=${PRISMRAG_EMAIL_FROM}"
  "PRISMRAG_BASE_URL=${PRISMRAG_BASE_URL}"
  "PRISMRAG_ADMIN_EMAILS=${PRISMRAG_ADMIN_EMAILS}"
  "PRISMRAG_ALERT_MIN_SEVERITY=ERROR"
  "PRISMRAG_LOG_LEVEL=${PRISMRAG_LOG_LEVEL}"
  "PRISMRAG_LOG_BLOB_ENABLED=${PRISMRAG_LOG_BLOB_ENABLED}"
  "PRISMRAG_LOG_BLOB_CONTAINER=${PRISMRAG_LOG_BLOB_CONTAINER}"
  "PRISMRAG_LOG_BLOB_FLUSH_SEC=300"
)

app_exists() {
  az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" &>/dev/null
}

if app_exists; then
  echo "    App exists — updating secrets, registry, image, env"
  az containerapp secret set \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --secrets "${SECRET_ARGS[@]}" \
    --output none

  az containerapp identity assign \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --user-assigned "$IDENTITY_ID" \
    --output none

  az containerapp registry set \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --server "$ACR_LOGIN_SERVER" \
    --identity "$IDENTITY_ID" \
    --output none

  az containerapp update \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --image "$IMAGE" \
    --replace-env-vars "${ENV_ARGS[@]}" \
    --output none
else
  echo "    Creating new container app"
  CREATE_ARGS=(
    --name "$APP_NAME"
    --resource-group "$RESOURCE_GROUP"
    --environment "$CONTAINER_ENV"
    --image "$IMAGE"
    --registry-server "$ACR_LOGIN_SERVER"
    --registry-identity "$IDENTITY_ID"
    --user-assigned "$IDENTITY_ID"
    --min-replicas "$MIN_REPLICAS"
    --max-replicas "$MAX_REPLICAS"
    --cpu "$CPU"
    --memory "$MEMORY"
    --secrets "${SECRET_ARGS[@]}"
    --env-vars "${ENV_ARGS[@]}"
    --output none
  )

  if [[ -n "$INGRESS" ]]; then
    CREATE_ARGS+=(--ingress "$INGRESS" --target-port "$TARGET_PORT")
  fi

  az containerapp create "${CREATE_ARGS[@]}"
fi

echo "    Done: ${APP_NAME}"
