# Azure deployment

**Status: written, not executed.** This document and `infra/provision.sh`
describe a real deployment path against Azure Container Apps, Azure
Database for PostgreSQL Flexible Server, and Microsoft Entra ID. None of it
has been run against a real Azure subscription — there is no Azure tenant
available in the sandbox this project was built in (its network egress
allowlist blocks every container registry, including Microsoft's own
`mcr.microsoft.com` — see the Docker note in the README). Read the script
before running it, and treat resource names/SKUs as a starting point, not a
prescription.

This intentionally does not deploy on its own. Provisioning cloud resources
and creating an Entra ID app registration are both real, billable, and
identity-sensitive actions that should be a decision the subscription owner
makes deliberately, not something that runs automatically from an AI
assistant's task list.

## Architecture

```
                          Azure Container Apps Environment
                          ┌─────────────────────────────────┐
Internet ──ingress──────▶ │  frontend (Streamlit)            │
Internet ──ingress──────▶ │  api (FastAPI)                   │──▶ Azure Database for
                          │  db-init (Container Apps Job,     │    PostgreSQL Flexible
                          │           runs once, loads data)  │    Server
                          └─────────────────────────────────┘
                                        │
                                        ▼
                          Azure Container Registry (image store)

Microsoft Entra ID  ──(App roles: HR.Analyst / HR.Manager / HR.Admin)──▶ api + frontend
```

Both `api` and `frontend` run the same image (this repo's `Dockerfile`) with
different start commands, exactly like `docker-compose.yml` does locally —
Azure Container Apps is the direct cloud equivalent of that compose file.
The frontend imports `app.api.copilot` directly rather than calling the API
app over HTTP (see `frontend/app.py`), so both need the same environment
variables; it doesn't need network access to the `api` app to function.

## Prerequisites

- An Azure subscription, and the [`az` CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) logged in (`az login`).
- Owner or Contributor role on the subscription/resource group you're deploying into.
- Global Administrator or Application Administrator in the target Entra ID tenant, to create the app registration in step 2 below.

## 1. Provision the Azure resources

```bash
export PG_ADMIN_PASSWORD="choose-a-strong-password"
./infra/provision.sh
```

This creates, in order: a resource group, an Azure Container Registry, an
Azure Database for PostgreSQL Flexible Server (Burstable B1ms — bump this
for real load), a Container Apps Environment, builds and pushes this
repo's image via `az acr build` (no local Docker needed), runs the schema +
synthetic-data load as a one-off Container Apps Job (the cloud equivalent of
the `db-init` service in `docker-compose.yml`), and creates the `api` and
`frontend` container apps.

By default `ANTHROPIC_API_KEY` and the `ENTRA_*` variables are deployed
empty, so the app comes up in the same demo-safe fallback mode described in
`.env.example`: rule-based NL parsing instead of the LLM, and the
token→role auth stub instead of real Entra ID. It's fully functional and
fully governed in that mode — steps 2–3 below are what turn on the
production-grade versions of each.

**Before running for real:** open `infra/provision.sh` and adjust
`LOCATION`, the resource names (several must be globally unique), and the
Postgres `--public-access` setting (wide open by default for a first
deploy; scope it to Container Apps' outbound IPs or a VNet integration for
anything beyond a demo).

## 2. Register the app in Entra ID

This part is manual and identity-sensitive, so it isn't scripted. Full
steps are in `app/governance/entra_auth.py`'s module docstring (the same
module that validates the resulting tokens) — summarized:

1. Entra ID → App registrations → New registration. Note the **Application
   (client) ID** and **Directory (tenant) ID**.
2. App roles → create three roles with these exact Value strings (the code
   maps on them): `HR.Analyst`, `HR.Manager`, `HR.Admin`.
3. Enterprise applications → this app → Users and groups → assign each real
   analyst/manager/admin to the matching role. **Don't assign anyone a role
   that grants individual-employee PII access — none of the three roles
   do**, by design (see `app/governance/permissions.py`'s `ROLE_TEMPLATES`).
4. For manager department-scoping, configure a `department` claim (Token
   configuration → optional claim, or a custom claims mapping policy). If
   the claim name isn't literally `department`, set `ENTRA_DEPARTMENT_CLAIM`
   to whatever it is.

## 3. Wire the real secrets in

```bash
az containerapp update -g people-analytics-copilot-rg -n people-analytics-copilot-api \
  --set-env-vars \
    ENTRA_TENANT_ID=secretref:entra-tenant-id \
    ENTRA_CLIENT_ID=secretref:entra-client-id \
    ANTHROPIC_API_KEY=secretref:anthropic-api-key

az containerapp secret set -g people-analytics-copilot-rg -n people-analytics-copilot-api \
  --secrets \
    entra-tenant-id=<tenant-id> \
    entra-client-id=<client-id> \
    anthropic-api-key=<key>
```

Repeat for the `frontend` app. Restart both revisions
(`az containerapp revision restart`) to pick up the change.

## CI/CD

`.github/workflows/ci.yml` runs the full test/eval/security suite against
both SQLite and PostgreSQL on every push and PR — see the README's
"Continuous integration" section. A deployment workflow
(`.github/workflows/cd.yml`) is included, gated behind `workflow_dispatch`
and a set of required repository secrets (`AZURE_CREDENTIALS`, `ACR_NAME`,
`AZURE_RESOURCE_GROUP`) — it builds the image, pushes it to ACR, and updates
the two container apps to the new image. It has never run, for the same
reason `provision.sh` hasn't: no Azure credentials exist in the environment
this project was built in. Configure those secrets and trigger it manually
the first time before trusting it on a schedule.

## What's been spot-checked vs. not

The `az postgres flexible-server create` flags in `provision.sh`
(`--sku-name`, `--tier Burstable`, `--version 16`, `--public-access`) were
checked against Microsoft's current documentation during this session and
match. The `az containerapp` commands (env-vars, `secretref:` syntax,
container app jobs) were not fully confirmed the same way — Microsoft's
docs pages for that CLI surface didn't return clean command examples via
automated fetch — though the syntax used is the long-standing documented
pattern. One thing worth knowing about before a real run: a still-open
Azure Container Apps issue describes `--env-vars ...=secretref:<name>`
sometimes getting rewritten to a different secret name by the platform
regardless of how it was set (CLI, YAML, or REST) —
[microsoft/azure-container-apps#1705](https://github.com/microsoft/azure-container-apps/issues/1705).
Worth checking whether that's still open before relying on the secret
wiring in `provision.sh`/`cd.yml` working exactly as written.

## Cost

Everything above is sized for a demo/evaluation deployment, not production
load: Postgres Burstable B1ms, Container Apps at 1–3 replicas with scale-to-
zero available if `--min-replicas 0` is set once traffic patterns are
known. Check the [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/)
before leaving this running unattended.
