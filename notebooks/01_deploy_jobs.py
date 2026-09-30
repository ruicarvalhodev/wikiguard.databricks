# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,Deploy jobs
dbutils.widgets.text("job_file", "", "job_file (empty = deploy all)")
_job_file = dbutils.widgets.get("job_file").strip()

from wikiguard.common.jobs import JOBS_DIR, deploy_all, upsert_job

if _job_file:
    results = [upsert_job(JOBS_DIR / _job_file)]
else:
    results = deploy_all()
    if not results:
        print(f"No job definitions found in {JOBS_DIR}")

for job_name, job_id, action in results:
    print(f"[deploy] {action} '{job_name}' (id={job_id}) — PAUSED")