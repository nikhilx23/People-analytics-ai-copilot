#!/usr/bin/env bash
# Provisions the Azure resources for the People Analytics AI Copilot:
# a resource group, an Azure Container Registry, an Azure Database for
# PostgreSQL Flexible Server, and two Azure Container Apps (api, frontend)
# on a shared Container Apps Environment.
#
# NOT EXECUTED. This script was written against the `az` CLI's documented
# command shapes but has not been run against a real Azure subscription -
# there is no Azure tenant available in the environment this project was
# built in. Read it before running it. It is meant to be run once,
# interactively, by whoever owns the Azure subscription this gets deployed
# into - see docs/AZURE_DEPLOYMENT.md for the full walkthrough, including
# the Entra ID app registration steps this script does NOT do (those are
# manual, in the Azure Portal, and documented in
# app/governance/entra_auth.py's module docstring and in the deployment doc).
#
# Usage:
#   az login
#   ./infra/provision.sh
#
# Idempotent-ish: `az ... create` commands are safe to re-run (Azure no-ops
# or updates in place for most of these resource types), but this has not
# been verified end-to-end since it has never been run.

set -euo pipefail

# --- Configuration - edit these before running -----------------------------
LOCATION="${LOCATION:-eastus}"
RESOURCE_GROUP="${RESOURCE_GROUP:-people-analytics-copilot-rg}"
ACR_NAME="${ACR_NAME:-peopleanalyticscopilotacr}"        # must be globally unique, alphanumeric only
PG_SERVER_NAME="${PG_SERVER_NAME:-people-analytics-copilot-pg}"  # must be globally unique
PG_ADMIN_USER="${PG_ADMIN_USER:-copilot_admin}"
PG_DB_NAME="${PG_DB_NAME:-people_analytics}"
CONTAINERAPPS_ENV="${CONTAINERAPPS_ENV:-people-analytics-copilot-env}"
API_APP_NAME="${API_APP_NAME:-people-analytics-copilot-api}"
FRONTEND_APP_NAME="${FRONTEND_APP_NAME:-people-analytics-copilot-frontend}"

# Set this before running, or export PG_ADMIN_PASSWORD in your shell -
# never hardcode a real password in this file.
PG_ADMIN_PASSWORD="${PG_ADMIN_PASSWORD:?Set PG_ADMIN_PASSWORD before running this script}"

echo "== Resource group =="
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION"

echo "== Container registry =="
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled true

echo "== PostgreSQL Flexible Server =="
# Burstable B1ms is the smallest tier - fine for a demo/eval deployment.
# Bump --sku-name / --tier for real production load.
az postgres flexible-server create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$PG_SERVER_NAME" \
  --location "$LOCATION" \
  --admin-user "$PG_ADMIN_USER" \
  --admin-password "$PG_ADMIN_PASSWORD" \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --storage-size 32 \
  --version 16 \
  --public-access 0.0.0.0-255.255.255.255  # tighten this to Container Apps' outbound IPs / a VNet integration for anything beyond a demo

az postgres flexible-server db create \
  --resource-group "$RESOURCE_GROUP" \
  --server-name "$PG_SERVER_NAME" \
  --database-name "$PG_DB_NAME"

DATABASE_URL="postgresql+psycopg2://${PG_ADMIN_USER}:${PG_ADMIN_PASSWORD}@${PG_SERVER_NAME}.postgres.database.azure.com:5432/${PG_DB_NAME}?sslmode=require"

echo "== Container Apps environment =="
az extension add --name containerapp --upgrade --yes || true
az provider register --namespace Microsoft.App --wait

az containerapp env create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINERAPPS_ENV" \
  --location "$LOCATION"

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)

echo "== Build + push image via ACR Tasks (no local Docker needed) =="
az acr build \
  --registry "$ACR_NAME" \
  --image "people-analytics-copilot:latest" \
  .

echo "== Load the schema + synthetic dataset into the new database =="
# Runs the same app/database/load_data.py used everywhere else in this
# project, as a one-off ACR-built container job rather than a long-lived
# app - mirrors what docker-compose.yml's db-init service does locally.
az containerapp job create \
  --resource-group "$RESOURCE_GROUP" \
  --name "people-analytics-copilot-db-init" \
  --environment "$CONTAINERAPPS_ENV" \
  --trigger-type Manual \
  --replica-timeout 300 \
  --image "${ACR_LOGIN_SERVER}/people-analytics-copilot:latest" \
  --command "python3" "-m" "app.database.load_data" \
  --env-vars "DATABASE_URL=${DATABASE_URL}" \
  --registry-server "$ACR_LOGIN_SERVER"

az containerapp job start \
  --resource-group "$RESOURCE_GROUP" \
  --name "people-analytics-copilot-db-init"

echo "== API container app =="
az containerapp create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$API_APP_NAME" \
  --environment "$CONTAINERAPPS_ENV" \
  --image "${ACR_LOGIN_SERVER}/people-analytics-copilot:latest" \
  --target-port 8000 \
  --ingress external \
  --registry-server "$ACR_LOGIN_SERVER" \
  --command "uvicorn" "app.api.main:app" "--host" "0.0.0.0" "--port" "8000" \
  --env-vars \
      "DATABASE_URL=${DATABASE_URL}" \
      "ANTHROPIC_API_KEY=secretref:anthropic-api-key" \
      "LLM_MODEL=claude-sonnet-5" \
      "ENTRA_TENANT_ID=secretref:entra-tenant-id" \
      "ENTRA_CLIENT_ID=secretref:entra-client-id" \
      "ENTRA_CLIENT_SECRET=secretref:entra-client-secret" \
  --secrets \
      "anthropic-api-key=${ANTHROPIC_API_KEY:-}" \
      "entra-tenant-id=${ENTRA_TENANT_ID:-}" \
      "entra-client-id=${ENTRA_CLIENT_ID:-}" \
      "entra-client-secret=${ENTRA_CLIENT_SECRET:-}" \
  --min-replicas 1 \
  --max-replicas 3

echo "== Frontend container app =="
# The Streamlit frontend imports app.api.copilot directly (see
# frontend/app.py) rather than calling the API app over HTTP, so it needs
# the same DATABASE_URL/ANTHROPIC_API_KEY/ENTRA_* env vars, not a link to
# the API app's URL.
az containerapp create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FRONTEND_APP_NAME" \
  --environment "$CONTAINERAPPS_ENV" \
  --image "${ACR_LOGIN_SERVER}/people-analytics-copilot:latest" \
  --target-port 8501 \
  --ingress external \
  --registry-server "$ACR_LOGIN_SERVER" \
  --command "streamlit" "run" "frontend/app.py" "--server.address" "0.0.0.0" "--server.port" "8501" \
  --env-vars \
      "DATABASE_URL=${DATABASE_URL}" \
      "ANTHROPIC_API_KEY=secretref:anthropic-api-key" \
      "LLM_MODEL=claude-sonnet-5" \
      "ENTRA_TENANT_ID=secretref:entra-tenant-id" \
      "ENTRA_CLIENT_ID=secretref:entra-client-id" \
      "ENTRA_CLIENT_SECRET=secretref:entra-client-secret" \
  --secrets \
      "anthropic-api-key=${ANTHROPIC_API_KEY:-}" \
      "entra-tenant-id=${ENTRA_TENANT_ID:-}" \
      "entra-client-id=${ENTRA_CLIENT_ID:-}" \
      "entra-client-secret=${ENTRA_CLIENT_SECRET:-}" \
  --min-replicas 1 \
  --max-replicas 2

echo
echo "Done. Frontend URL:"
az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FRONTEND_APP_NAME" \
  --query "properties.configuration.ingress.fqdn" -o tsv

echo "API URL:"
az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$API_APP_NAME" \
  --query "properties.configuration.ingress.fqdn" -o tsv

echo
echo "Next: register the app in Entra ID (App roles: HR.Analyst / HR.Manager"
echo "/ HR.Admin, department claim) and set ENTRA_TENANT_ID/ENTRA_CLIENT_ID/"
echo "ENTRA_CLIENT_SECRET as real values (this script deploys with them"
echo "empty by default, so the app runs in demo-token auth mode until you"
echo "do). See docs/AZURE_DEPLOYMENT.md and app/governance/entra_auth.py."
