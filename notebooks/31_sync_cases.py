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

# DBTITLE 1,Sync gold candidates into Lakebase cases
from wikiguard.config import CONFIG
from wikiguard.lakebase.sync import sync_cases

dbutils.widgets.text("lookback_hours", "24", "lookback_hours")
lookback_hours = int(dbutils.widgets.get("lookback_hours"))

summary = sync_cases(spark, CONFIG, lookback_hours=lookback_hours)

print(f"  Candidates:      {summary['candidates']}")
print(f"  Inserted:         {summary['inserted']}")
print(f"  Already present:  {summary['already_present']}")