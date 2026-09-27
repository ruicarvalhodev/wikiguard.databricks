# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,Print resolved configuration
from wikiguard.config import CONFIG

print("=" * 60)
print("WikiGuard -- resolved configuration")
print("=" * 60)
for _field, _value in [
    ("catalog",         CONFIG.catalog),
    ("project",         CONFIG.project),
    ("bronze_schema",   CONFIG.bronze_schema),
    ("silver_schema",   CONFIG.silver_schema),
    ("gold_schema",     CONFIG.gold_schema),
    ("landing_volume",  CONFIG.landing_volume),
    ("bronze_table",    CONFIG.bronze_table),
    ("volume_path",     CONFIG.volume_path),
    ("events_path",     CONFIG.events_path),
    ("checkpoint_path", CONFIG.checkpoint_path),
    ("schema_path",     CONFIG.schema_path),
    ("wikis",           ", ".join(CONFIG.wikis)),
    ("contact_email",   CONFIG.contact_email),
]:
    print(f"  {_field:<18}: {_value}")
print("=" * 60)

# COMMAND ----------

# DBTITLE 1,Step 1 — Verify catalog access
# ---------------------------------------------------------------------------
# Step 1 -- Verify catalog access
# The catalog is an environment prerequisite.  This project does NOT create it.
# ---------------------------------------------------------------------------
print(f"[1/5] Verifying catalog access: {CONFIG.catalog} ...")
try:
    spark.sql(f"USE CATALOG `{CONFIG.catalog}`")
    print(f"      USE CATALOG {CONFIG.catalog!r:<35} OK")
except Exception as _e:
    print(f"      USE CATALOG {CONFIG.catalog!r:<35} FAILED")
    raise RuntimeError(
        f"Cannot access catalog '{CONFIG.catalog}'.  "
        "This catalog is an environment prerequisite and must be provisioned "
        "before running this notebook.  Contact your instructor.\n"
        f"  Cause: {_e}"
    ) from _e

# COMMAND ----------

# DBTITLE 1,Step 2 — Create schemas
# ---------------------------------------------------------------------------
# Step 2 -- Create schemas (IF NOT EXISTS -- safe to re-run)
# Schemas may already exist from a previous run or if pre-created by the
# instructor.  Neither case is an error.
# ---------------------------------------------------------------------------
print(f"[2/5] Creating schemas in {CONFIG.catalog!r} ...")
for _schema in (CONFIG.bronze_schema, CONFIG.silver_schema, CONFIG.gold_schema):
    _fqn = f"{CONFIG.catalog}.{_schema}"
    try:
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CONFIG.catalog}`.`{_schema}`")
        print(f"      CREATE SCHEMA {_fqn!r:<45} OK")
    except Exception as _e:
        print(f"      CREATE SCHEMA {_fqn!r:<45} FAILED")
        raise RuntimeError(
            f"Cannot create schema '{_fqn}'.  In a shared catalog you need "
            "CREATE SCHEMA privilege.  Ask your instructor to grant it.\n"
            f"  Cause: {_e}"
        ) from _e

# COMMAND ----------

# DBTITLE 1,Step 3 — Create landing Volume
# ---------------------------------------------------------------------------
# Step 3 -- Create landing Volume (IF NOT EXISTS -- safe to re-run)
# ---------------------------------------------------------------------------
_vol_fqn = f"{CONFIG.catalog}.{CONFIG.bronze_schema}.{CONFIG.landing_volume}"
print(f"[3/5] Creating volume {_vol_fqn!r} ...")
try:
    spark.sql(
        f"CREATE VOLUME IF NOT EXISTS "
        f"`{CONFIG.catalog}`.`{CONFIG.bronze_schema}`.`{CONFIG.landing_volume}`"
    )
    print(f"      CREATE VOLUME {_vol_fqn!r:<45} OK")
except Exception as _e:
    print(f"      CREATE VOLUME {_vol_fqn!r:<45} FAILED")
    raise RuntimeError(
        f"Cannot create volume '{_vol_fqn}'.  "
        f"Check CREATE VOLUME privilege on schema '{CONFIG.bronze_schema}'.\n"
        f"  Cause: {_e}"
    ) from _e

# COMMAND ----------

# DBTITLE 1,Step 4 — Create Volume sub-directories
# ---------------------------------------------------------------------------
# Step 4 -- Create sub-directories inside the Volume
# ---------------------------------------------------------------------------
print(f"[4/5] Creating sub-directories in {CONFIG.volume_path} ...")
for _subdir in (CONFIG.events_path, CONFIG.checkpoint_path, CONFIG.schema_path):
    dbutils.fs.mkdirs(_subdir)
    print(f"      mkdirs {_subdir!r:<52} OK")

# COMMAND ----------

# DBTITLE 1,Step 5 — Smoke test
# ---------------------------------------------------------------------------
# Step 5 -- Smoke test: write -> read -> delete
# ---------------------------------------------------------------------------
_smoke = f"{CONFIG.volume_path}/_setup_smoke_test.txt"
print(f"[5/5] Running volume smoke test ...")
try:
    dbutils.fs.put(_smoke, "ok", overwrite=True)
    _content = dbutils.fs.head(_smoke).strip()
    assert _content == "ok", f"expected 'ok', got {_content!r}"
    dbutils.fs.rm(_smoke)
    print(f"      write/read {CONFIG.volume_path!r:<45} OK")
except Exception as _e:
    print(f"      write/read {CONFIG.volume_path!r:<45} FAILED")
    raise RuntimeError(
        f"Cannot write to or read from '{CONFIG.volume_path}'.\n"
        f"  Full path: {_smoke}\n"
        f"  Cause: {_e}"
    ) from _e

# COMMAND ----------

# DBTITLE 1,Environment ready
print("\n[setup] WikiGuard environment ready.")
print(f"         catalog : {CONFIG.catalog}")
print(f"         schemas : {CONFIG.bronze_schema}, {CONFIG.silver_schema}, {CONFIG.gold_schema}")
print(f"          volume : {CONFIG.volume_path}")