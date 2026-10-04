# WikiGuard

WikiGuard is a real-time Wikipedia edit integrity triage system built as a Databricks capstone project.
It ingests every Wikipedia edit via Wikimedia EventStreams, enriches suspicious edits with the Lift Wing
revert-risk ML model, maintains a work queue in Lakebase Postgres, and provides an AI agent and a
Streamlit reviewer app so human editors can triage, assign, annotate, and resolve cases.
The full pipeline runs continuously with no manual steps once deployed.

> **Live app:** [wikiguard](https://wikiguard-1352785079224954.aws.databricksapps.com)
> (Databricks App `wikiguard`)
>
> Opening it requires signing in to the bootcamp Databricks workspace and the app's

## Architecture

```
Wikimedia EventStreams (SSE)
  → connector job → Volume (JSONL)
    → bronze (Auto Loader) → wikiguard_bronze.raw_recentchange
      → silver (parse + filter) → wikiguard_silver.edits → candidates
        → scoring job (every 5 min)
            → Lift Wing API → edit_risk → gold.triage_candidates
            → Lakebase sync → cases (Postgres)
              ← AI agent + reviewers via Streamlit app
                → case writes → CDF → lb_cases_history / lb_agent_actions_history
                  → analytics job → gold analytics tables → Analytics page
```

## Prerequisites

- Databricks workspace with **serverless compute**
- Shared catalog `bootcamp_students` (provisioned by the training environment — do **not** create or drop it)
- A **SQL warehouse** (the app and agent query Delta tables through it)
- Foundation Model endpoints `databricks-claude-sonnet-5-5` and `databricks-gte-large-en` enabled in the workspace
- The **Lakebase Change Data Feed** preview feature enabled for the workspace

## Setup

Complete each step in order.

### 1. Schemas and Volume — `notebooks/00_setup`

Run `notebooks/00_setup` from the workspace once.  It creates the bronze, silver, and gold schemas and the
landing Volume with `IF NOT EXISTS` guards.  Safe to re-run at any time.

### 2. Secrets — `notebooks/03_create_secrets`

Run `notebooks/03_create_secrets` and fill in the three widgets:

| Widget | Secret path | Value |
| --- | --- | --- |
| `contact_email` | `wikiguard/contact_email` | A real operator email (required by Wikimedia bot policy T400119) |
| `lakebase_url` | `wikiguard/lakebase_url` | Full Postgres connection string (see step 3) |
| `sql_warehouse_id` | `wikiguard/sql_warehouse_id` | Warehouse ID from the SQL Warehouse UI |

### 3. Lakebase

1. In the Databricks UI, create a Lakebase project named `wikiguard`.
2. Open branch `production`, enable **password authentication**, create the role `wikiguard_app` with a password.
3. Copy the connection string: `postgresql://wikiguard_app:<password>@<host>/databricks_postgres?sslmode=require`
4. Paste it into the `lakebase_url` widget in `notebooks/03_create_secrets` and run the notebook.
5. Run `notebooks/30_lakebase_setup` — creates all tables, the update trigger, and seeds the demo reviewers.
   Safe to re-run (every statement is idempotent).

### 4. Jobs — `notebooks/01_deploy_jobs`

Run `notebooks/01_deploy_jobs` from the workspace (leave the `job_file` widget blank to deploy all jobs).
Every deploy leaves jobs **paused**.  After deploying, resume these three jobs from the Jobs UI in order:

1. `wikiguard-ingest-connector` — feeds the Volume continuously
2. `wikiguard-bronze-autoloader` — bronze + silver loop
3. `wikiguard-score-candidates` — scoring + Lakebase sync + analytics (every 5 min)

### 5. Change Data Feed

In the Lakebase UI, start the CDF on schema `public`, targeting `bootcamp_students.wikiguard_bronze`.
This populates `lb_cases_history` and `lb_agent_actions_history` which power the Analytics page.

### 6. App

1. In the Apps UI, create a new app from the repo root.
2. Add these four resources **before** deploying:

   | Resource | Type | Key | Environment variable |
   | --- | --- | --- | --- |
   | Operator contact email | Secret — `wikiguard/contact_email` | `contact-email` | `WIKIGUARD_CONTACT_EMAIL` |
   | Lakebase connection URL | Secret — `wikiguard/lakebase_url` | `lakebase-url` | `WIKIGUARD_LAKEBASE_URL` |
   | SQL warehouse | SQL Warehouse | `sql-warehouse` | `WIKIGUARD_SQL_WAREHOUSE_ID` |
   | Agent model endpoint | Serving endpoint `databricks-claude-sonnet-5-5` | *(no key)* | *(grants Can Query only)* |

3. Deploy from the repo root (`app.yaml` tells the runtime `streamlit run app/main.py`).
4. Grant the app service principal `USE SCHEMA` and `SELECT` on all three schemas:
   ```sql
   GRANT USE SCHEMA ON SCHEMA bootcamp_students.wikiguard_bronze TO `<application-id>`;
   GRANT SELECT    ON SCHEMA bootcamp_students.wikiguard_bronze TO `<application-id>`;
   -- repeat for wikiguard_silver and wikiguard_gold
   ```
   The application ID appears on the **System check** page after the first deploy.
5. Open the app and navigate to **System check** — all eight rows should show ✅.

## Configuration

### Environment variables and secrets

| Name | Type | Source | Who reads it |
| --- | --- | --- | --- |
| `WIKIGUARD_CONTACT_EMAIL` | Env var (from secret) | Secret `wikiguard/contact_email` | `config.py`; every Wikimedia HTTP request |
| `WIKIGUARD_LAKEBASE_URL` | Env var (from secret) | Secret `wikiguard/lakebase_url` | `lakebase/client.py` |
| `WIKIGUARD_SQL_WAREHOUSE_ID` | Env var | SQL Warehouse resource (app) / secret (notebooks) | `agent/sql.py` |
| `DATABRICKS_CLIENT_ID` | Env var | Set automatically by Databricks Apps runtime | System check identity display |
| `STREAMLIT_SERVER_PORT` | Env var | Set automatically by Databricks Apps runtime | Streamlit startup |
| `STREAMLIT_SERVER_ADDRESS` | Env var | Set automatically by Databricks Apps runtime | Streamlit startup |

### Config dataclass fields (`src/wikiguard/config.py`)

| Field | Default | Description |
| --- | --- | --- |
| `catalog` | `bootcamp_students` | Shared catalog; provided by the environment, not created by this project |
| `project` | `wikiguard` | Prefix driving all derived schema names (`wikiguard_bronze`, etc.) |
| `landing_volume` | `landing` | UC Volume name for raw file landing |
| `contact_email` | *(required — no default)* | Operator email for Wikimedia User-Agent header; raises `ValueError` if missing |
| `wikis` | `["enwiki","dewiki","frwiki"]` | Wikimedia project IDs to monitor |
| `agent_model` | `databricks-claude-sonnet-5-5` | Foundation Model endpoint for the AI agent |
| `embedding_endpoint` | `databricks-gte-large-en` | Embedding model for similarity search |

## Secrets handling

Three values are stored as Databricks secrets (scope `wikiguard`) and never committed to the repository:

- **`contact_email`** — required by Wikimedia’s bot policy (T400119).  Every HTTP request to Wikimedia must include
  a real operator address in both `User-Agent` and `Api-User-Agent`.  Requests without it return 403.
- **`lakebase_url`** — contains the Postgres password for the `wikiguard_app` role.  Stored as a secret so
  the password is never visible in notebooks, job logs, or the repository.
- **`sql_warehouse_id`** — avoids hard-coding a resource ID that changes between workspaces.

Each secret is read at call time (not at import), so a misconfigured app fails loudly at the first use
of that resource, not silently at startup.

The app service principal must have READ permission on the `wikiguard` scope to access the secrets
through its resource mappings in `app.yaml`.

## Operations

### What runs continuously

| Job | Mode | Notebook |
| --- | --- | --- |
| `wikiguard-ingest-connector` | Continuous | `10_ingest_connector` |
| `wikiguard-bronze-autoloader` | Continuous (AvailableNow loop) | `11_bronze_autoloader` |
| `wikiguard-score-candidates` | Scheduled every 5 min | `21_score_candidates` + parallel `22_enrich_diffs`, `31_sync_cases`, `50_analytics` |

The scoring job has a 15-minute timeout per task.  The connector and bronze jobs run in continuous mode:
Databricks keeps exactly one run active at all times and restarts automatically if it ends.

### Pausing everything

1. Open the Jobs UI and pause all three jobs.
2. Wait for active runs to finish (up to 15 minutes for the scoring job).

### After a long pause

**Delete the connector checkpoint before resuming.**  The connector stores its last-seen event ID at
`/Volumes/bootcamp_students/wikiguard_bronze/landing/checkpoints/connector/last_event_id`.
Leaving it in place after a multi-day pause causes the connector to replay every event since
that timestamp, which can take hours and causes the latency metric to show days.

To reset: delete the checkpoint file from the Volume UI or a notebook cell, then resume.

## Measured results

Fill in after a representative production run.

   | Metric | Value | Requirement |
   | --- | --- | --- |
   | Events in bronze | 16,462,138 | > 1,000,000 rows |
   | End-to-end latency p50 | 27.5 s | — |
   | End-to-end latency p95 | 43.5 s | < 60 s |
   | Cases in the review queue | 3,196 | — |
   | Agent tool calls (total) | 115 | — |

Measured on 4 October 2026, over one hour of live ingestion (gold.vw_bronze_latency); latency = Wikipedia edit time (meta.dt) to arrival in bronze (ingest_ts).

## Known limitations

- **Lift Wing language coverage** — the revert-risk model only scores languages on its canonical list.
  Edits from wikis like `mag`, `als`, or `zh-yue` return a 400 error; they are stored in `edit_risk_errors`
  and not retried.
- **CDF snapshot** — changes made to Lakebase cases before Change Data Feed was enabled appear only as
  a single snapshot row, not as individual transitions.  The Analytics page notes this.
- **No per-user login** — the “Acting as” selector in the sidebar substitutes for proper authentication;
  all reviewer actions are attributed to whoever is selected in the dropdown.
- **Brute-force similarity** — `find_similar_cases` uses cosine similarity computed over all rows in
  `silver.edit_diffs`; it will slow down as the table grows beyond tens of thousands of embeddings.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Wikimedia returns 403 on all requests | Both `User-Agent` and `Api-User-Agent` headers must contain a real contact email.  Check `WIKIGUARD_CONTACT_EMAIL` is set. |
| Bronze job fails with “trigger not supported” on serverless | Only `trigger(availableNow=True)` works on serverless.  The job uses an AvailableNow batch with a 10-second Python sleep.  Do not add `processingTime` triggers. |
| `numInputRows` is `None` in bronze metrics | Structured Streaming on serverless does not expose `numInputRows` in progress metrics.  Row counts come from a `COUNT(*)` on the table instead. |
| `must be owner of table` in Lakebase | Run DDL as `wikiguard_app`, not as the superuser.  `notebooks/30_lakebase_setup` connects as `wikiguard_app` for all `CREATE TABLE` and `GRANT` statements. |
| Lift Wing returns 400 | Unsupported wiki language.  The scoring notebook writes the error to `edit_risk_errors` and skips the edit; no retry. |
| Warehouse checks fail on System check | The app service principal needs `USE SCHEMA` and `SELECT` on all three schemas.  Grant using the application ID shown on the System check page. |
| Queue or Case page actions do nothing | Streamlit’s nested-button anti-pattern.  Each action uses `st.form` with a single `st.form_submit_button`; validation runs outside the button handler. |

---

## Repository layout

```
wikiguard.databricks/
├── app/                    # Databricks App (Streamlit)
│   ├── main.py             # Entry point, navigation, sidebar reviewer selector
│   ├── data.py             # Lakebase + warehouse data helpers (all cached)
│   └── pages/              # queue, case, chat, analytics, system_check
├── jobs/                   # Job JSON definitions (deployed via 01_deploy_jobs)
├── notebooks/
│   ├── _bootstrap.py       # %run this first in every notebook
│   ├── 00_setup.py         # One-time catalog / schema / Volume setup
│   ├── 01_deploy_jobs.py   # Deploy or update jobs
│   ├── 03_create_secrets.py
│   ├── 10_ingest_connector.py
│   ├── 11_bronze_autoloader.py
│   ├── 21_score_candidates.py
│   ├── 22_enrich_diffs.py
│   ├── 30_lakebase_setup.py
│   ├── 31_sync_cases.py
│   └── 50_analytics.py
├── scripts/
│   └── deploy_jobs.py      # CLI equivalent of 01_deploy_jobs
├── src/
│   └── wikiguard/          # Importable Python package
│       ├── config.py       # Frozen dataclass Config + CONFIG singleton
│       ├── common/         # HTTP utilities (User-Agent, retries)
│       ├── ingest/         # SSE connector, Auto Loader, bronze
│       ├── transform/      # Silver and gold transforms
│       ├── enrich/         # Lift Wing API and diff embeddings
│       ├── lakebase/       # Lakebase schema, client, reverse ETL
│       ├── agent/          # AI agent tools, runner, audit log
│       └── analytics/      # CDF → Delta gold analytics tables
└── tests/
    └── unit/               # Cluster-free, network-free unit tests
```

## Developer reference

### Bootstrap pattern

Every notebook must start with `%run ./_bootstrap` as its first cell.  `_bootstrap.py` discovers the
project root, adds `<root>/src` to `sys.path`, and enables `%autoreload 2`.

| Priority | Strategy | Works when |
| --- | --- | --- |
| 1 | `dbutils.notebook.entry_point` context | Interactive Databricks notebook (validated for this workspace) |
| 2 | `os.getcwd()` walk-up | Workspace Files feature active |
| 3 | `os.getcwd()` as-is | Last resort — prints a warning |

`%autoreload 2` known limits: does not reliably reload dataclasses or enums with structural changes;
does not discover new modules.  Restart the Python kernel when in doubt.

### Running the tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Tests run entirely locally without a Spark cluster or network access.

### Syncing to the workspace

```bash
databricks sync --watch . /Users/<you>@<domain>/wikiguard.databricks
```

Or: Workspace → wikiguard.databricks → Git → Pull.

WikiGuard is a real-time Wikipedia integrity triage system built as a Databricks capstone
project. It consumes the Wikimedia EventStreams (SSE), filters suspicious edit candidates,
enriches them with the Wikimedia Lift Wing ML API, and feeds a work queue backed by
Lakebase Postgres on which an AI agent acts.

## Repository layout

```
wikiguard.databricks/
├── app/                    # Databricks App (Streamlit) — future
├── jobs/                   # Job JSON definitions (deployed via scripts/deploy_jobs.py)
├── notebooks/
│   ├── _bootstrap.py       # Run this first in every notebook: %run ./_bootstrap
│   └── 00_setup.py         # One-time catalog / volume setup
├── scripts/
│   └── deploy_jobs.py      # Create or update jobs using the Databricks SDK
├── src/
│   └── wikiguard/          # Importable Python package
│       ├── config.py       # Frozen dataclass Config + module-level CONFIG singleton
│       ├── common/         # Shared HTTP utilities and Spark schemas
│       ├── ingest/         # SSE connector, Auto Loader, bronze tables
│       ├── transform/      # Silver and gold transformations
│       ├── enrich/         # Lift Wing API calls and ML scoring
│       ├── lakebase/       # Lakebase Postgres schema and reverse ETL
│       ├── agent/          # AI agent tools and orchestration
│       └── analytics/      # Change Data Feed -> Delta aggregate tables
└── tests/
    └── unit/               # Cluster-free, network-free unit tests
```

## Syncing to the workspace

This folder is a plain workspace directory kept in sync with a Git remote via the
Databricks Git Folders (Repos) integration.

```bash
# Push local changes with the Databricks CLI
databricks sync --watch . /Users/<you>@<domain>/wikiguard.databricks
```

Or use the Git Folder UI: **Workspace -> wikiguard.databricks -> Git -> Pull**.

## Environment prerequisites

The catalog `bootcamp_students` is **shared with all students in the bootcamp**
and is provisioned by the training environment.  This project does **not** create
it.  If you cannot access it, contact your instructor or workspace admin.

All three schemas and the Volume are created by `notebooks/00_setup.py` with
`IF NOT EXISTS`, so the setup notebook is safe to re-run at any time.

| Resource | Full name | Notes |
| --- | --- | --- |
| Catalog | `bootcamp_students` | Shared; do not create or drop |
| Bronze schema | `bootcamp_students.wikiguard_bronze` | `wikiguard_` prefix avoids naming collisions |
| Silver schema | `bootcamp_students.wikiguard_silver` | `wikiguard_` prefix avoids naming collisions |
| Gold schema | `bootcamp_students.wikiguard_gold` | `wikiguard_` prefix avoids naming collisions |
| Volume | `/Volumes/bootcamp_students/wikiguard_bronze/landing` | Raw file landing area |

The `wikiguard_` prefix on schema names is intentional: since the catalog is
shared with other students' projects, the prefix scopes all resources to this
project and prevents name collisions.

## Bootstrap pattern

**Every notebook must start with `%run ./_bootstrap` as its very first cell.**

`_bootstrap.py` does three things in order:
1. Discovers the project root without any hard-coded path.
2. Adds `<root>/src` to `sys.path` so `import wikiguard` works.
3. Enables `%autoreload 2` so edits to `src/` are picked up immediately.

```python
# Cell 1 -- always first
%run ./_bootstrap

# Cell 2 -- wikiguard is now importable
from wikiguard.config import CONFIG
print(CONFIG)
```

## Root discovery method

`_bootstrap.py` uses a three-level fallback chain:

| Priority | Strategy | Works when |
| --- | --- | --- |
| 1 | `dbutils.notebook.entry_point` context | Interactive Databricks notebook (validated for this workspace) |
| 2 | `os.getcwd()` walk-up | Workspace Files feature is active (CWD = notebook directory) |
| 3 | `os.getcwd()` as-is | Last resort -- prints a warning; verify the root manually |

**Validated for this workspace**: Strategy 1 (dbutils context) is the primary method.
It resolves `/Workspace/Users/ruimigueltcarvalho@gmail.com/wikiguard.databricks`
correctly for all interactive notebooks. Strategy 2 applies when `os.getcwd()`
returns the notebook directory (Workspace Files active).

## When the code does not update

`%autoreload 2` reloads changed source files automatically, but it has known limits:

- **Dataclasses and enums** -- structural changes (new fields, removed fields, changed
  defaults) are not reliably picked up. The old class definition stays in the
  interpreter's memory.
- **New modules** -- a module that did not exist at the time of the first import is not
  discovered automatically. Import it explicitly after creating the file.
- **When in doubt** -- detach and re-attach the cluster (or restart the Python kernel).
  This is the only guaranteed reset. A `%reset` does not help because `sys.path` changes
  are not persisted across a kernel reset.

## Running the tests

Tests run entirely locally without a Spark cluster or network access.

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run all tests with verbose output
pytest tests/ -v

# Run a single test module
pytest tests/unit/test_config.py -v
```

## Jobs

Job definitions live as JSON in `jobs/`.  **Primary deploy route: run
`notebooks/01_deploy_jobs` from the workspace** — set the `job_file` widget
empty to deploy all jobs, or enter a filename (e.g. `ingest_connector.json`)
to update one job.  Locally, `scripts/deploy_jobs.py` provides the same
functionality via the Databricks CLI.

| Job | Notebook | Description |
| --- | --- | --- |
| `wikiguard-ingest-connector` | `10_ingest_connector` | Reads Wikimedia EventStreams and writes JSONL files to the Volume |
| `wikiguard-bronze-autoloader` | `11_bronze_autoloader` | Each iteration runs three steps: bronze (Auto Loader), silver edits (parse + MERGE), silver candidates (filter + tier) |
| `wikiguard-score-candidates` | `21_score_candidates` → `22_enrich_diffs` + `31_sync_cases` | Scheduled every 5 min, three tasks: (1) scores tier A/B candidates with Lift Wing → edit_risk → gold.triage_candidates; (2) fetches MediaWiki diffs + fills embeddings via ai_query → silver.edit_diffs; (3) copies new candidates into Lakebase `cases` with `ON CONFLICT DO NOTHING` — existing cases keep their status, assignment and notes. Tasks 2 and 3 run in parallel after task 1. |

Both jobs run in **continuous mode**: Databricks keeps exactly one run active
at all times and restarts automatically if the run ends or fails.

> **Why the bronze job uses an AvailableNow loop:** serverless compute does
> not support time-based streaming triggers (`processingTime`) or the default
> no-trigger mode — only `Trigger.AvailableNow` is available.  `bronze.py`
> runs an AvailableNow batch, then sleeps 10 seconds in plain Python, and
> repeats, keeping latency under one minute without any unsupported trigger.

Malformed silver rows (unparseable JSON, bad timestamp, missing wiki) go to `edits_quarantine` with a `reason` column instead of being silently dropped.

To rebuild `silver.candidates` after changing the tier rules: `DROP TABLE bootcamp_students.wikiguard_silver.candidates`, delete the `candidates/` subfolder inside the checkpoint Volume (`/Volumes/bootcamp_students/wikiguard_bronze/landing/checkpoints/`), then resume the job — the next iteration rebuilds it from `silver.edits`.

`silver.edit_diffs` stores the parsed diff text and a 1024-dim embedding for each scored edit. `wikiguard.enrich.similarity.find_similar_cases(spark, config, wiki, rev_id, k=5)` queries this table with brute-force cosine similarity and returns the k nearest neighbours joined to the gold queue — the agent's find-similar tool (task 4.1b) calls it directly.

**Every deploy leaves jobs paused** — the deploy script forces `pause_status: PAUSED` regardless of what the JSON file says. To start a job after deploying, open it in the Jobs UI and click **Resume**. Re-running the deploy notebook while a job is running will pause it.

> **Do not run `notebooks/10_ingest_connector` by hand while the job is active.**
> Two connectors writing to the same `events/` folder will double-write events.
> Pause the job before testing the notebook interactively.

## Analytics

Lakebase writes Change Data Feed (CDF) events to Delta tables in `wikiguard_bronze`,
one `lb_<table>_history` table per Postgres table.  Each row carries `_pg_change_type`
(`insert`, `update_preimage`, `update_postimage`, `delete`), `_pg_lsn`, `_pg_xid`
(0 for the initial snapshot), and a `_sort_by` ordering key.

**Flow:** Lakebase → CDF → `wikiguard_bronze.lb_cases_history` →
`wikiguard.analytics.case_events.process_once` → `wikiguard_silver.case_events`
→ `wikiguard.analytics.build.build_analytics` → gold tables below.

`notebooks/50_analytics` runs every 5 minutes as an independent task of the
`wikiguard-score-candidates` job, in parallel with the scoring tasks.

| Table | Description |
| --- | --- |
| `wikiguard_gold.fact_case_transitions` | One row per case status change; includes `from_status`, `to_status`, and `seconds_in_previous_state`. |
| `wikiguard_gold.fact_agent_activity` | One row per agent tool call from `lb_agent_actions_history`, with latency and a parsed `ok` boolean. |
| `wikiguard_gold.agg_agent_daily` | Daily roll-up of agent tool calls: call count, error rate, avg and p95 latency, distinct sessions. |
| `wikiguard_gold.agg_daily_triage` | Daily triage activity per tier: cases created, transitions into escalated/resolved/dismissed, median hours to close. |
| `wikiguard_gold.pipeline_health` | Monitoring table: one row appended per run with row counts and CDF lag in seconds. |

To rebuild `silver.case_events` from scratch: drop the table and delete
`/Volumes/bootcamp_students/wikiguard_bronze/landing/checkpoints/case_events/`.
The next run re-processes the full `lb_cases_history` history.

## Secrets

| Scope | Key | How to create |
| --- | --- | --- |
| `wikiguard` | `contact_email` | Run `notebooks/03_create_secrets` once; type the operator email into the widget |
| `wikiguard` | `sql_warehouse_id` | Run `notebooks/03_create_secrets`; paste the SQL warehouse ID into the `sql_warehouse_id` widget |

The email is embedded in every `User-Agent` header sent to Wikimedia (required by their bot policy, task T400119). It is stored as a Databricks secret rather than committed to the repository.

The Databricks App (task 6.8) will need READ permission on the `wikiguard` scope. Grant it in the Secrets UI after the app is created.

## Config fields

All configuration lives in `src/wikiguard/config.py` as a single frozen dataclass.

| Field | Default | Description |
| --- | --- | --- |
| `catalog` | `bootcamp_students` | Shared catalog -- provided by the environment, not created by this project |
| `project` | `wikiguard` | Project prefix; drives all derived schema names (`wikiguard_bronze` etc.) |
| `landing_volume` | `landing` | UC Volume for raw file landing |
| `contact_email` | *(required)* | Operator e-mail for the Wikimedia User-Agent header -- no valid default |
| `wikis` | `["enwiki","dewiki","frwiki"]` | Wikimedia project IDs to monitor |

`contact_email` has **no valid default**. Constructing `Config()` raises `ValueError`.
This is intentional: Wikimedia's bot policy requires an identifiable operator address
in the `User-Agent` header (task T400119).

Derived read-only properties:

| Property | Pattern | Example |
| --- | --- | --- |
| `bronze_schema` | `{project}_bronze` | `wikiguard_bronze` |
| `silver_schema` | `{project}_silver` | `wikiguard_silver` |
| `gold_schema` | `{project}_gold` | `wikiguard_gold` |
| `bronze_table` | `{catalog}.{bronze_schema}.raw_recentchange` | `bootcamp_students.wikiguard_bronze.raw_recentchange` |
| `volume_path` | `/Volumes/{catalog}/{bronze_schema}/{landing_volume}` | `/Volumes/bootcamp_students/wikiguard_bronze/landing` |
| `events_path` | `{volume_path}/events` | |
| `checkpoint_path` | `{volume_path}/checkpoints` | |
| `schema_path` | `{volume_path}/schema` | |
| `connector_checkpoint_file` | `{checkpoint_path}/connector/last_event_id` | |

## Lakebase

The operational review queue lives in a **Lakebase Postgres 17** instance
dedicated to this project.

| Resource | Value |
| --- | --- |
| Project | `wikiguard` |
| Branch | `production` |
| Database | `databricks_postgres` |
| Schema | `public` |

### Application role

All tables are created and owned by the native Postgres role **`wikiguard_app`**,
which connects with a static password.  The connection string has the form:

    postgresql://wikiguard_app:<password>@<host>/databricks_postgres?sslmode=require

### Secret

| Scope | Key | How to create |
| --- | --- | --- |
| `wikiguard` | `lakebase_url` | Run `notebooks/03_create_secrets`; paste the full connection string into the `lakebase_url` widget |

The URL is read at call time by `wikiguard.lakebase.client.get_lakebase_url()`.
Locally, export `WIKIGUARD_LAKEBASE_URL` instead of using the secret vault.

### Schema setup

Run `notebooks/30_lakebase_setup` once to apply the schema and seed the demo
reviewers.  The notebook is safe to re-run — every statement is idempotent.

| Object | Description |
| --- | --- |
| `case_status` | Enum: `open`, `in_review`, `escalated`, `resolved`, `dismissed` |
| `reviewers` | Human reviewers with optional per-wiki focus areas |
| `cases` | Work-queue entries mirroring `gold.triage_candidates` |
| `case_notes` | Human and agent notes attached to a case |
| `watchlists` / `watchlist_pages` | Per-reviewer page watchlists |
| `agent_actions` | Audit log of every tool call made by the AI agent |

A BEFORE UPDATE trigger (`trg_cases_before_update`) on `cases` stamps
`updated_at` on every update and manages `resolved_at` automatically when
`status` transitions to or from `resolved` / `dismissed` — agent write tools
need only change `status`.

`REPLICA IDENTITY FULL` is set on `cases`, `case_notes`, and `agent_actions`
so the Change Data Feed (Phase 5) carries full before/after row images.

## App

The WikiGuard frontend is a Streamlit app deployed via Databricks Apps.  Its
entry point is `app/main.py`; the `app.yaml` at the repo root tells the
runtime how to start it.

### Resources

Create these four resources in the App UI **before** deploying.  The resource
**key** is what you type in the UI; `app.yaml` maps each key to an environment
variable via `valueFrom`.

| Resource | Type | Key | Environment variable |
| --- | --- | --- | --- |
| Operator contact email | Secret (`wikiguard/contact_email`) | `contact-email` | `WIKIGUARD_CONTACT_EMAIL` |
| Lakebase connection URL | Secret (`wikiguard/lakebase_url`) | `lakebase-url` | `WIKIGUARD_LAKEBASE_URL` |
| SQL warehouse | SQL Warehouse | `sql-warehouse` | `WIKIGUARD_SQL_WAREHOUSE_ID` |
| Agent model endpoint | Serving endpoint (`databricks-claude-sonnet-5-5`) | *(no key needed)* | *(grants **Can Query** only — no env var)* |

The first three secrets and the warehouse resolve to their values at startup;
the model endpoint resource only grants the app service principal **Can Query**
permission on the serving endpoint — `DatabricksOpenAI()` picks up the
credential automatically.

### `app.yaml` mapping

```yaml
command: ['streamlit', 'run', 'app/main.py']

env:
  - name: WIKIGUARD_CONTACT_EMAIL
    valueFrom: contact-email
  - name: WIKIGUARD_LAKEBASE_URL
    valueFrom: lakebase-url
  - name: WIKIGUARD_SQL_WAREHOUSE_ID
    valueFrom: sql-warehouse
```

The Databricks Apps runtime automatically sets `STREAMLIT_SERVER_PORT` and
`STREAMLIT_SERVER_ADDRESS=0.0.0.0`; no manual port wiring is needed.

### After deploying

Open the **System check** page first.  It runs all eight connectivity and
configuration checks (identity, environment variables, Lakebase, three
warehouse tables, model endpoint, MediaWiki API) and shows ✅ or ❌ for each.

The page displays the app service principal's **application ID** prominently
at the top — this is the ID you need when writing Unity Catalog `GRANT`
statements.  The hints next to each ❌ tell you exactly what to fix.
