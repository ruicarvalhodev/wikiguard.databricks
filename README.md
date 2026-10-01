# WikiGuard

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
| `wikiguard-score-candidates` | `21_score_candidates` → `22_enrich_diffs` | Scheduled every 5 min, two sequential tasks: (1) scores tier A/B candidates with Lift Wing → edit_risk → gold.triage_candidates; (2) fetches MediaWiki diffs + fills embeddings via ai_query → silver.edit_diffs |

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

## Secrets

| Scope | Key | How to create |
| --- | --- | --- |
| `wikiguard` | `contact_email` | Run `notebooks/03_create_secrets` once; type the operator email into the widget |

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
