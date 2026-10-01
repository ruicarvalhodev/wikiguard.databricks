# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,Create wikiguard secret scope and store secrets
from databricks.sdk import WorkspaceClient

# Deliberately not importing wikiguard.config: building CONFIG reads secrets,
# which don't exist until this notebook has run.
SCOPE = "wikiguard"   # must match Config.secret_scope in config.py

dbutils.widgets.text("contact_email", "", "contact_email")
dbutils.widgets.text("lakebase_url",  "", "lakebase_url")

email        = dbutils.widgets.get("contact_email").strip()
lakebase_url = dbutils.widgets.get("lakebase_url").strip()

if not email and not lakebase_url:
    raise ValueError(
        "Fill in at least one widget before running: "
        "contact_email and/or lakebase_url."
    )

wc = WorkspaceClient()

# Create the scope if it doesn't already exist
try:
    wc.secrets.create_scope(SCOPE)
    print(f"Created secret scope '{SCOPE}'")
except Exception as exc:
    if "already exists" in str(exc).lower():
        print(f"Secret scope '{SCOPE}' already exists")
    else:
        raise

# Store each secret only when its widget is non-empty
if email:
    wc.secrets.put_secret(scope=SCOPE, key="contact_email", string_value=email)
    print(f"Secret '{SCOPE}/contact_email' stored successfully")

if lakebase_url:
    wc.secrets.put_secret(scope=SCOPE, key="lakebase_url", string_value=lakebase_url)
    print(f"Secret '{SCOPE}/lakebase_url' stored successfully")

print("(Secret values are not printed to avoid leaving them in cell output)")