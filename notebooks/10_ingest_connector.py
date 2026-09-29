# Databricks notebook source
# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,Configure and run connector
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

dbutils.widgets.text("max_seconds", "300", "max_seconds (empty = forever)")
_raw = dbutils.widgets.get("max_seconds").strip()
max_seconds = float(_raw) if _raw else None

from wikiguard.ingest.connector import run

stats = run(max_seconds=max_seconds)
print(stats)