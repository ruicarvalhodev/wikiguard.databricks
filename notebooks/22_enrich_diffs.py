# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Install BeautifulSoup4
# MAGIC %pip install beautifulsoup4 -q

# COMMAND ----------

# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,Enrich pending diffs and fill embeddings
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

dbutils.widgets.text("max_rows", "50", "max_rows")
_max_rows = int(dbutils.widgets.get("max_rows") or "200")

from wikiguard.enrich.diffs import enrich_pending
from wikiguard.config import CONFIG

summary = enrich_pending(spark, CONFIG, max_rows=_max_rows)
print(summary)