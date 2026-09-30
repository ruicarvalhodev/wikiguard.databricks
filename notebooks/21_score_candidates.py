# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,Score candidates and rebuild triage queue
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

dbutils.widgets.text("max_rows", "20", "max_rows")
_max_rows = int(dbutils.widgets.get("max_rows") or "300")

from wikiguard.enrich.scoring import score_pending
from wikiguard.transform.gold import build_triage_candidates
from wikiguard.config import CONFIG

summary = score_pending(spark, CONFIG, max_rows=_max_rows)
print(summary)

n = build_triage_candidates(spark, CONFIG)
print(f"gold.triage_candidates: {n} rows")