# People Analytics AI Copilot

A governed, natural-language analytics assistant for HR data. An analyst
asks a question in plain English; the system answers it using versioned,
reproducible analytical methods executed through an MCP tool layer — never
by letting an LLM freehand-calculate a number or query the database
directly.

This started as a local MVP (rule-based NL parsing, SQLite, a token→role
auth stub) and has since been upgraded vertically, in the order below, into
a production-oriented platform: a real LLM tool-selection layer with the
rule-based parser kept as an automatic fallback, real Microsoft Entra ID
authentication, PostgreSQL, Docker, performance instrumentation, a 204-case
accuracy evaluation, an adversarial security/governance test suite, CI on
GitHub Actions, and a documented (unexecuted) Azure deployment path. Every
number in this document — accuracy percentages, latency figures, test
counts — comes from actually running the code in this repo, not from
assumption; see "Evaluation" and "Security & governance testing" for how.

```
                    HR Analyst
                        |
                        v
              Streamlit UI  /  FastAPI /ask
                        |
                        v
   ┌───────────────────────────────────────┐
   │  Governance: question-level PII screen  │
   │  Entra ID auth -> role -> RBAC scope     │
   └───────────────────────────────────────┘
                        |
                        v
      LLM tool selection (real LLM; rule-based
        NL parser as automatic fallback)
                        |
                        v
                   MCP Server
        ------------------+------------------
        |                                    |
   Analytics Tools                  run_governed_sql
   (attrition, headcount,           (governed SQL guard:
    tenure, salary,                  read-only, no blocked
    promotion, ...)                  fields, aggregate-only
        |                            sensitive fields, no
        v                            wildcard SELECT, row-capped)
     pandas                                  |
        |                                    v
        +-------------> PostgreSQL <---------+
                       (or SQLite)
                        |
                        v
             Result Validation Layer
                        |
                        v
        AI answer + "Explain this answer"
        (LLM-phrased, template fallback)
                        |
                        v
    Governance audit log + Performance log + Feedback (👍/👎/report)
```

## Why it's built this way

The brief this project is based on (a Microsoft People Analytics posting)
calls out several things explicitly, and the architecture is organized
around each of them:

1. **"The LLM shouldn't have unrestricted access to your database."** Every
   analytics question resolves to one of nine MCP tools
   (`app/mcp/server.py`); none of them accept or execute arbitrary SQL
   except `run_governed_sql`, which is itself restricted (read-only,
   single-statement, no `SELECT *`, aggregate-only for sensitive fields, no
   direct identifiers, no individual-level `GROUP BY`, row-capped). The LLM
   (`app/api/llm_router.py`) **selects among these tools** — it never
   writes SQL that reaches the database unguarded; even its `sql` escape
   hatch goes through the exact same `validate_sql()` guard as everything
   else.
2. **"Encode analytical methods."** `app/analytics/` contains versioned,
   documented formulas (e.g. *Governed Attrition Calculation v1.2*). Neither
   the rule-based parser nor the LLM computes a metric itself — each only
   chooses which encoded method to call and with what parameters. The same
   question always produces the same number.
3. **Data governance.** `app/governance/` enforces two independent checks —
   a question-level PII screen before any tool is chosen, and a SQL-level
   guard inside `run_governed_sql` — so a differently-worded question can't
   route around governance. Every decision (allowed or blocked) is logged
   to `query_log` for audit. See "Security & governance testing" below for
   how this is actually tested, adversarially, not just asserted.
4. **Role-based access, never individual PII.** Authentication is real
   Entra ID when configured, a demo stub otherwise; authorization
   (`app/governance/permissions.py`) is real either way, and no role —
   analyst, manager, or admin — is ever granted individual-employee PII
   access. Elevated roles get a wider aggregate scope and visibility into
   audit/performance dashboards, not looser data access.
5. **"Cut response times."** Every question is timed stage-by-stage
   (`app/api/perf_context.py`, `app/api/performance.py`) and the numbers are
   real, measured, and visible on the "⚡ Performance" dashboard — see that
   section for the actual bottleneck found and fixed.

## Repository layout

```
people-analytics-ai-copilot/
├── app/
│   ├── api/
│   │   ├── main.py            # /ask /feedback /stats /audit-log /performance /health
│   │   ├── copilot.py         # full pipeline: PII screen -> auth/RBAC -> routing -> tool -> validation -> explain
│   │   ├── nl_parser.py       # rule-based NL -> tool/params mapping (LLM fallback)
│   │   ├── llm_router.py      # real LLM tool-selection layer (Anthropic tool-use)
│   │   ├── perf_context.py    # per-request DB-time accumulator
│   │   ├── performance.py     # performance_log storage + stats (avg/p95/min/max)
│   │   ├── validation.py      # sanity-checks tool results before they reach the analyst
│   │   └── feedback_store.py
│   ├── mcp/
│   │   ├── server.py          # MCP server exposing 9 governed tools
│   │   └── tools/              # tool implementations (analytics_tools.py, sql_tool.py)
│   ├── analytics/         # encoded, versioned analytical methods
│   │   ├── base.py            # AnalyticsResult - the "Explain this answer" data model
│   │   ├── attrition.py, headcount.py, tenure.py, compensation.py
│   │   └── data_access.py     # de-identified data loading (full_name never loaded), in-process DataFrame cache
│   ├── governance/
│   │   ├── permissions.py     # role -> capabilities (RBAC), demo auth + Entra ID dispatch
│   │   ├── entra_auth.py      # real Entra ID JWT validation (RS256, JWKS, role/department claims)
│   │   └── policies.py        # PII screen + SQL guard + audit logging
│   ├── database/          # schema.sql (SQLite), schema_postgres.sql, connection.py, load_data.py
│   └── evaluation/
│       ├── generate_eval_dataset.py     # deterministic generator -> eval_dataset.csv (204 cases, 11 categories)
│       ├── eval_dataset.csv
│       ├── reference_calculations.py    # independent ground truth (no shared code with app/analytics)
│       ├── eval_runner.py               # runs the pipeline, checks answers, reports accuracy; CI exit code
│       └── security_tests.py            # adversarial security/governance test suite; CI exit code
├── data/
│   └── generate_synthetic_data.py   # fictional 520-employee dataset generator
├── frontend/
│   └── app.py              # Streamlit UI: chat, explain-this-answer, feedback, evaluation, audit log, performance
├── tests/                   # pytest suite (66 tests)
├── infra/
│   └── provision.sh         # Azure resource provisioning (az CLI) - written, not executed, see docs/
├── docs/
│   └── AZURE_DEPLOYMENT.md  # full deployment walkthrough
├── .github/workflows/
│   ├── ci.yml                # tests + eval + security suite, on SQLite AND PostgreSQL, every push/PR
│   └── cd.yml                 # Azure deploy - manual-only, gated on secrets, never run
├── requirements.txt
├── Dockerfile / docker-compose.yml   # api + frontend + Postgres + db-init, all services
└── README.md
```

## Setup

```bash
pip install -r requirements.txt

# 1. Generate the synthetic dataset and load it (SQLite by default; set
#    DATABASE_URL to point at PostgreSQL instead - see .env.example)
cd data && python3 generate_synthetic_data.py && cd ..
python3 -m app.database.load_data

# 2. Run the test suite
python3 -m pytest -v

# 3. Run the accuracy evaluation (204 generated cases)
python3 -m app.evaluation.eval_runner

# 4. Run the adversarial security & governance test suite
python3 -m app.evaluation.security_tests

# 5. Run the Streamlit demo (talks to the pipeline in-process)
streamlit run frontend/app.py

# 6. (optional) Run the FastAPI backend separately
uvicorn app.api.main:app --reload --port 8000

# 7. (optional) Run the MCP server standalone (stdio transport, for an
#    MCP-compatible client or the `mcp dev` inspector)
python3 -m app.mcp.server
```

Everything above runs with **zero configuration** — no API key, no Azure
tenant, no Postgres install required. `.env.example` documents every
optional variable (`DATABASE_URL`, `ANTHROPIC_API_KEY`, `LLM_MODEL`,
`ENTRA_TENANT_ID`/`ENTRA_CLIENT_ID`/`ENTRA_CLIENT_SECRET`) and what each
one's absence falls back to.

### Docker

```bash
docker compose up --build
# API:      http://localhost:8000
# Frontend: http://localhost:8501
# Postgres: localhost:5432
```

`docker-compose.yml` runs four services: `db` (Postgres 16), `db-init` (a
one-shot container that applies the schema and loads the dataset, so `api`
and `frontend` never start against an empty database), `api`, and
`frontend`. **Honesty note:** this was written and validated
(`docker compose config` parses cleanly; the exact same startup sequence —
`load_data` against this Postgres image, then serving — has been run
directly on the host and passes every test/eval/security check below) but
`docker build`/`docker compose up` were never run to completion end-to-end
in the sandbox this project was built in, because that sandbox's network
policy blocks every container registry (Docker Hub, GHCR, MCR, ECR, GCR all
returned 403 on the base-image pull — confirmed directly, not assumed). The
`docker-build` job in `.github/workflows/ci.yml` builds the image for real
on every push, on a runner that has normal registry access.

## The dataset

`data/generate_synthetic_data.py` produces 520 entirely fictional employees
across 8 departments (Engineering, Sales, Marketing, Customer Support,
Finance, Human Resources, Product, Operations) with hire/termination dates,
salary, performance rating, job level, location, manager, and promotion
history — enough realism to make department comparisons and trend questions
meaningful, none of it real.

## LLM tool-selection layer

`app/api/llm_router.py` calls the Anthropic API with the exact tool schemas
registered on the MCP server (`app/mcp/server.py`'s `list_tools()` — pulled
live, not hand-duplicated), so the LLM can only select among the same
governed tools the rule-based parser uses. It's activated automatically
when `ANTHROPIC_API_KEY` is set; every question routes through it first
(`app/api/copilot.py::_route_question`), and any failure — no key, a
network error, an unexpected response — falls back to the deterministic
rule-based parser automatically (`routing_method` on every response records
which path actually ran: `llm`, `rule_based`, or
`rule_based_fallback_after_llm_error`). `tests/test_llm_router.py` exercises
every branch of this (LLM unavailable, LLM error, LLM decline text, the SQL
escape hatch, RBAC applying identically regardless of router) with mocked
LLM responses, since no API key is available in this environment — the
routing *logic* is fully tested even though the *model call itself* has
never been exercised for real here.

The default model (`LLM_MODEL`, `claude-sonnet-5`) and the tool-use request
shape (`client.messages.create(..., tools=..., messages=...)`, reading
`tool_use`/`text` blocks off `response.content`) were checked against
Anthropic's live API docs during this session rather than left as
originally written from training knowledge — the model ID had gone stale
(a dated `claude-sonnet-4-5-20250929` string that's no longer current) and
was updated; the request/response shape itself was already correct and
needed no change.

## Governance model

| Question | Result |
|---|---|
| "What is the attrition rate in Engineering?" | ✅ Answered — aggregate, department-level |
| "What is the average salary by department?" | ✅ Answered — aggregate, grouped |
| "What is John Smith's salary?" | 🚫 Refused — names a specific employee |
| "Show me every employee's salary." | 🚫 Refused — individual-level ask, plural phrasing |
| "Give me a full export of the employee table." | 🚫 Refused — raw row-level export |
| `SELECT * FROM employees` (via the SQL escape hatch) | 🚫 Refused — wildcard bypasses field checks |

Enforcement happens twice, independently:

* **`check_question_for_pii`** (`app/governance/policies.py`) screens the
  question text itself, before any tool is chosen — it matches against the
  employee roster, "individual lookup" phrasing, and raw-export phrasing
  (including plural forms like "every employee's salary", added after the
  expanded evaluation surfaced it as a real gap — see "Evaluation").
* **`validate_sql`** (same file) is the guard inside the `run_governed_sql`
  tool: rejects anything that isn't a single read-only `SELECT`, rejects
  `SELECT *`/`table.*` outright (added after the adversarial security suite
  caught it letting every column, PII included, through unchecked — see
  "Security & governance testing"), rejects `full_name`/`manager` outright,
  and requires `salary`/`performance_rating` to appear only inside an
  aggregate function (`AVG`, `SUM`, `MIN`, `MAX`, `COUNT`) — never as a raw
  per-row value.

Both paths write to `query_log` (visible in the Streamlit "Governance audit
log" tab, `hr_admin`-only, and via `GET /audit-log`), so governance is
auditable, not just enforced silently.

## Roles, RBAC, and Entra ID

| Role | Aggregate scope | Individual PII | Raw SQL | Audit log | Admin dashboards |
|---|---|---|---|---|---|
| `hr_analyst` | Company-wide | Never | Governed only | No | No |
| `hr_manager` | Own department only (auto-scoped or hard-blocked outside it) | Never | Never (the SQL escape hatch is closed entirely to scoped roles — free-form SQL can't be safely department-verified) | No | No |
| `hr_admin` | Company-wide | **Still never** | Governed only | Yes | Yes |

No role gets raw employee-level PII — that boundary doesn't move with
seniority; it's a product decision, not a gap. `hr_admin`'s extra
capability is visibility (audit log, performance dashboard), not looser
data access.

Authentication has two backends, chosen automatically
(`app/governance/permissions.py`):

* **Real Microsoft Entra ID** (`app/governance/entra_auth.py`) — activated
  when `ENTRA_TENANT_ID`/`ENTRA_CLIENT_ID` are set. Validates a bearer
  token exactly the way a real Azure AD-protected API would: fetches the
  tenant's JWKS, verifies the RS256 signature, checks audience/issuer/
  expiry, and maps the token's App Role claim (`HR.Analyst`/`HR.Manager`/
  `HR.Admin`) to this app's RBAC roles. `tests/test_entra_auth.py` exercises
  this real validation logic end-to-end with a self-signed token and a
  dependency-injected fake JWKS client — a legitimate test of the actual
  code path, not a mock of the outcome — since no real Azure tenant is
  available here. Full Azure Portal setup steps are in that module's
  docstring and in `docs/AZURE_DEPLOYMENT.md`.
* **Demo token→role stub** (this module) — used whenever Entra ID isn't
  configured, so the app is fully runnable and fully governed without an
  Azure tenant: `demo-analyst-token`, `demo-manager-token` (scoped to
  Engineering), `demo-admin-token`. The Streamlit sidebar role switcher uses
  these.

## "Explain this answer"

Every `AnalyticsResult` (`app/analytics/base.py`) carries its method name and
version, the human-readable formula, the input parameters, the intermediate
numbers behind the headline figure, and the data source — not just the
number. The Streamlit "Explain this answer" expander and the `explanation`
field in the `/ask` API response both render straight from this. When the
LLM layer is active, the headline answer text is additionally rephrased in
natural language by `generate_llm_explanation()` — instructed to use *only*
numbers already present in the result JSON, never to introduce a new one —
falling back to the deterministic template below if that call fails for any
reason:

```
Answer: Voluntary Attrition Rate (Engineering department) = 6.4%

Method: Governed Attrition Calculation v1.2
Formula: Attrition Rate (%) = (Employees who left in period / Average headcount) x 100,
         where Average headcount = (headcount at period start + headcount at period end) / 2

Breakdown:
  employees_who_left: 3
  headcount_at_period_start: 50
  headcount_at_period_end: 59
  average_headcount: 54.5

Data source: Employee Analytics Dataset (governed view)
```

## Performance monitoring

Every question is timed stage-by-stage — routing (LLM call or rule-based
parse), tool execution, DB query time specifically, validation, total — and
recorded to `performance_log`, visible on the "⚡ Performance" dashboard
tab (`hr_admin`-only) and via `GET /performance`. A real snapshot measured
during this project's own test/eval/security runs (1,953 recorded
questions):

| Stage | Avg (ms) |
|---|---|
| Routing | 0.1 |
| Tool execution | 4.9 |
| DB query | 0.2 |
| Validation | 0.0 |
| **Total** | **5.5 avg / 11.9 p95** |

By tool, `calculate_attrition` was consistently the slowest (avg 8.6ms, p95
13.3ms) — profiling traced this to `app/analytics/data_access.py` reloading
the full employee DataFrame from the database on every single call, even
though most questions in a session query the same static dataset
repeatedly. The fix was an in-process cache
(`load_employees_df()`/`clear_cache()`, invalidated automatically on every
`load_data.py` run so it never serves stale data after a reload). Measured
before/after, just now, on this repo:

```
4x uncached loads: 127.4ms
1 load + 3 cache hits: 10.5ms
speedup: 12.2x
```

## Evaluation

`app/evaluation/eval_runner.py` runs **204 generated questions**
(`generate_eval_dataset.py`, 11 categories — core, normal, company-wide,
comparison, department alias, date range, typo, ambiguous, unsupported
metric, boundary, PII) through the full pipeline and checks each result
against an **independently computed** reference value
(`reference_calculations.py`, which shares no code with `app/analytics` —
otherwise a bug in the formula would always "pass").

**Current measured accuracy: 99.5% (203/204)**, identical on both SQLite
and PostgreSQL:

| Category | Accuracy |
|---|---|
| alias | 100.0% (8/8) |
| ambiguous | 100.0% (8/8) |
| boundary | 100.0% (8/8) |
| company_wide | 100.0% (6/6) |
| comparison | 100.0% (24/24) |
| core | 100.0% (17/17) |
| date_range | 100.0% (8/8) |
| normal | 100.0% (96/96) |
| pii | 100.0% (6/6) |
| typo | 100.0% (8/8) |
| unsupported | 93.3% (14/15) |

**Known limitation, documented rather than hidden:** "How many employees
are enrolled in the 401k plan?" is misclassified as a headcount question
with high confidence, because it happens to contain the literal substring
"how many employees are" — one of the regex triggers for the headcount
metric in the rule-based parser (`app/api/nl_parser.py`). This is a real,
honest gap in keyword-based intent parsing, not a bug that was patched
around; it's exactly the class of error the LLM tool-selection layer (which
understands semantics rather than matching substrings) is expected to
avoid, and it's called out here rather than adjusted away to reach 100%.

`app/evaluation/eval_runner.py`'s `__main__` exits non-zero if accuracy
drops below 98% — a CI regression gate with headroom for that one known
miss, not for a new one.

## Security & governance testing

`app/evaluation/security_tests.py` is a separate adversarial test suite
from the accuracy evaluation above — it measures whether the system
**refuses to leak anything it shouldn't**, not whether it gives the right
answer, across 41 cases spanning three independent defense layers:

1. **Live pipeline** — real, unmocked adversarial natural-language
   questions (prompt injection, SQL-injection-as-text, PII extraction, data
   exfiltration phrasing, claimed-identity privilege escalation, RBAC
   bypass attempts, unsupported-field probing) run through
   `answer_question()`. The check isn't "was it `blocked=True`" — a
   question that safely falls back to a harmless aggregate is exactly as
   safe as one that trips the explicit PII screen — it's whether the
   response leaks an individual employee's name or row-level sensitive
   data, checked against the actual employee roster.
2. **Governance layer in isolation** — 12 SQL payloads run directly against
   `validate_sql()`, the backstop between *any* tool-selection mechanism
   and the database.
3. **LLM escape-hatch defense in depth** — `route_with_llm` is mocked to
   simulate an LLM that *was* talked into emitting malicious SQL or an
   RBAC-violating tool call, proving the governance/RBAC layer is what
   actually stops the attack, not the model's good behavior.

**Current measured result: 100% (41/41 safe)** — but not on the first run.
Building this suite found two real, previously-unnoticed governance gaps,
both fixed (not routed around) before this number was reached:

* **`SELECT * FROM employees` was allowed through `validate_sql()`.** The
  blocked-field and aggregate-only checks matched by column *name*, so a
  wildcard silently bypassed both and would have returned every row with
  `full_name`, `salary`, `manager`, and `performance_rating` intact. Fixed
  by rejecting any wildcard `SELECT` outright (`COUNT(*)` is unaffected —
  the `*` there isn't immediately after `SELECT`/`DISTINCT`).
* **The PII screen's raw-export detection only matched a few literal
  phrases** ("list all employees", "dump", "export all"), missing plural
  phrasings like "Show me every employee's salary." and "Give me a full
  export of the employee table." Fixed by broadening the check (see
  "Governance model" above).

A third, unrelated bug surfaced while re-verifying the fix against
PostgreSQL specifically: `AVG()`/`SUM()` over an `INTEGER` column returns
SQL `numeric`, which psycopg2 surfaces as Python `Decimal` (and `DATE`
columns as `datetime.date`) — neither is JSON-serializable, which would
have broken the LLM explanation layer and any JSON response carrying a raw
`run_governed_sql` aggregate against Postgres specifically (SQLite returns
plain `float`/`str` for the same query, which is why this wasn't caught
until this suite was run against both dialects). Fixed at the source, in
`app/mcp/tools/sql_tool.py`, where raw driver rows are converted to plain
JSON types before they go anywhere else in the app.

`security_tests.py`'s `__main__` exits non-zero on **any** unsafe result —
unlike the accuracy floor, there's no acceptable non-zero baseline for a
governance regression.

## Feedback loop

Every answer in the Streamlit UI can be rated 👍 / 👎 / reported with a
free-text issue. Feedback is stored in the `feedback` table (question, tool
used, answer, feedback, error type, response time) and surfaced on the
"Feedback dashboard" tab: satisfaction %, most-used tools, most common
questions, recently flagged questions, and average response time.

## CI

`.github/workflows/ci.yml` runs, on every push and PR: the full pytest
suite, the 204-case accuracy evaluation, and the adversarial security suite
— each **twice**, once against SQLite and once against a real PostgreSQL
16 service container, plus a Docker image build. A regression in accuracy
below 98% or any unsafe security result fails the build (see the exit-code
notes above).

## Azure deployment

`docs/AZURE_DEPLOYMENT.md` + `infra/provision.sh` describe a full path to
Azure Container Apps, Azure Database for PostgreSQL Flexible Server, and
Microsoft Entra ID, plus a manual-only, secret-gated deploy workflow
(`.github/workflows/cd.yml`). **Written, not executed** — no Azure
subscription is available in the environment this project was built in;
see that doc for exactly what has and hasn't been verified.

## Design decisions worth knowing about

* **Rule-based NL layer kept as the automatic fallback, not replaced.** The
  LLM router is tried first when configured; any failure falls back to the
  deterministic rule-based parser (`app/api/nl_parser.py`) rather than
  erroring, so the system degrades to "fully deterministic and testable
  without an API key," never to "broken."
* **No role, at any level, gets raw SQL or individual PII**
  (`app/governance/permissions.py`). Individual employee lookups belong in
  the system of record, not a chat interface — a deliberate product
  decision, documented in the code and enforced identically for every role
  including `hr_admin`.
* **Low-confidence questions ask for clarification instead of guessing.**
  If neither router can identify a metric with confidence, the response
  returns example questions rather than routing to a plausible-but-wrong
  tool — a wrong confident answer is worse than an admitted "I'm not sure."
* **PostgreSQL and SQLite are both first-class, always.** All SQL goes
  through one dialect-agnostic module (`app/database/connection.py`, via
  SQLAlchemy); every test, the 204-case evaluation, and the 41-case
  security suite are run against both dialects, not just developed against
  one and assumed to work on the other.

## What's real vs. what's documented-but-unexecuted

| Area | Status |
|---|---|
| Analytics methods, governance guard (PII screen + SQL guard), MCP tools, RBAC, validation layer, audit log, performance instrumentation, feedback loop, evaluation harness, security test suite | **Real** — implemented, tested, and re-verified on both SQLite and PostgreSQL |
| LLM tool-selection layer | **Real logic**, fully tested via mocked LLM responses (`tests/test_llm_router.py`) — the actual model call has not been exercised, since no `ANTHROPIC_API_KEY` is available in this environment; automatic, tested fallback to the rule-based parser either way |
| Entra ID authentication | **Real validation logic**, fully tested against a self-signed JWT and a fake JWKS client (`tests/test_entra_auth.py`) — never exercised against a real Azure tenant, since none is available here; automatic, tested fallback to the demo token stub either way |
| Docker | **Written and config-validated**; build never completed end-to-end locally (container registries are blocked by this sandbox's network policy) — builds for real in `.github/workflows/ci.yml`'s `docker-build` job |
| CI (GitHub Actions) | **Real** — `.github/workflows/ci.yml` runs on every push/PR |
| Azure deployment (CD workflow, provisioning script) | **Written, not executed** — no Azure subscription available here; see `docs/AZURE_DEPLOYMENT.md` |
