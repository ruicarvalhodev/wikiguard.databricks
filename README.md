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
| `wikiguard-bronze-autoloader` | `11_bronze_autoloader` | Auto Loader loop that ingests JSONL files into the bronze Delta table |

Both jobs run in **continuous mode**: Databricks keeps exactly one run active
at all times and restarts automatically if the run ends or fails.

> **Why the bronze job uses an AvailableNow loop:** serverless compute does
> not support time-based streaming triggers (`processingTime`) or the default
> no-trigger mode — only `Trigger.AvailableNow` is available.  `bronze.py`
> runs an AvailableNow batch, then sleeps 10 seconds in plain Python, and
> repeats, keeping latency under one minute without any unsupported trigger.

To pause a job without deleting it:

```bash
databricks jobs update <job-id> --json '{"continuous": {"pause_status": "PAUSED"}}'
```

Or toggle it in the Jobs UI.

> **Do not run `notebooks/10_ingest_connector` by hand while the job is active.**
> Two connectors writing to the same `events/` folder will double-write events.
> Pause the job before testing the notebook interactively.

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
