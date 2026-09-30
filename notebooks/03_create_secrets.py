# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,Create wikiguard secret scope and contact_email secret
from databricks.sdk import WorkspaceClient

# Deliberately not importing wikiguard.config: building CONFIG reads this secret,
# which doesn't exist until this notebook has run.
SCOPE = "wikiguard"   # must match Config.secret_scope in config.py

dbutils.widgets.text("contact_email", "", "contact_email")
email = dbutils.widgets.get("contact_email").strip()
if not email:
    raise ValueError("Enter your operator email in the contact_email widget before running.")

wc    = WorkspaceClient()
scope = SCOPE

# Create the scope if it doesn't already exist
try:
    wc.secrets.create_scope(scope)
    print(f"Created secret scope '{scope}'")
except Exception as exc:
    if "already exists" in str(exc).lower():
        print(f"Secret scope '{scope}' already exists")
    else:
        raise

# Store the contact email (safe to re-run -- overwrites the previous value)
wc.secrets.put_secret(scope=scope, key="contact_email", string_value=email)
print(f"Secret '{scope}/contact_email' stored successfully")
print("(The email value is not printed to avoid leaving it in cell output)")