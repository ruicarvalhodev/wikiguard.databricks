# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Install psycopg (must precede bootstrap — %pip restarts Python)
# MAGIC %pip install "psycopg[binary]" -q

# COMMAND ----------

# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,Apply schema and seed reviewers
from wikiguard.lakebase.setup import apply_schema, seed_reviewers

apply_schema()
seed_reviewers()

# COMMAND ----------

# DBTITLE 1,Row counts — verify every table was created
from wikiguard.lakebase.client import connect

TABLES = [
    "reviewers",
    "cases",
    "case_notes",
    "watchlists",
    "watchlist_pages",
    "agent_actions",
]

with connect() as conn:
    with conn.cursor() as cur:
        for table in TABLES:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            (count,) = cur.fetchone()
            print(f"  {table:<20} {count:>6} rows")