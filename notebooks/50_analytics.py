# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,Run analytics pipeline
from wikiguard.analytics.case_events import process_once
from wikiguard.analytics.build import build_analytics
from wikiguard.config import CONFIG

rows_added = process_once(spark, CONFIG)
print(f"case_events rows appended: {rows_added}")

summary = build_analytics(spark, CONFIG, rows_added)
print("\nGold table row counts:")
for table, count in summary.items():
    print(f"  {table}: {count}")