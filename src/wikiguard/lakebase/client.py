"""
Lakebase Postgres connection helpers.

``get_lakebase_url()`` reads the connection string at call time — not at import
time — following the same Databricks-vs-local pattern as ``load_contact_email``
in ``config.py``.

``connect()`` returns a live psycopg v3 connection.  The caller owns the
connection lifecycle::

    with lakebase.connect() as conn:   # commits on exit, rolls back on error
        with conn.cursor() as cur:
            cur.execute("SELECT 1")

Note
----
psycopg (v3) is not pre-installed on Databricks serverless.  Notebooks that
call ``connect()`` must install it **before** importing this module, in their
very first cell (before ``%run ./_bootstrap``, since ``%pip`` restarts Python)::

    %pip install "psycopg[binary]" -q
"""
from __future__ import annotations

import os

import psycopg


def get_lakebase_url() -> str:
    """
    Return the Lakebase connection URL.

    Resolution order
    ----------------
    1. ``WIKIGUARD_LAKEBASE_URL`` env var — lets local scripts skip the vault.
    2. Databricks secret ``wikiguard/lakebase_url`` — when
       ``DATABRICKS_RUNTIME_VERSION`` is set.

    Raises
    ------
    RuntimeError
        If the URL cannot be found.  The message points at
        ``notebooks/03_create_secrets`` as the fix.
    """
    if url := os.environ.get("WIKIGUARD_LAKEBASE_URL"):
        return url

    if os.environ.get("DATABRICKS_RUNTIME_VERSION"):
        try:
            from databricks.sdk.runtime import dbutils  # noqa: PLC0415
            return dbutils.secrets.get("wikiguard", "lakebase_url")
        except Exception as exc:
            raise RuntimeError(
                "Could not read secret 'wikiguard/lakebase_url'.  "
                "Run notebooks/03_create_secrets to set it up."
            ) from exc

    # Inside a Databricks App, a missing env var is always a misconfiguration.
    if os.environ.get("DATABRICKS_APP_NAME"):
        raise RuntimeError(
            "WIKIGUARD_LAKEBASE_URL is not set.  "
            "Add a secret resource with key 'lakebase-url' to the app "
            "and map it to WIKIGUARD_LAKEBASE_URL in app.yaml."
        )

    raise RuntimeError(
        "No Lakebase URL found.  Set the WIKIGUARD_LAKEBASE_URL env var "
        "for local development, or run notebooks/03_create_secrets on Databricks."
    )


def connect() -> psycopg.Connection:
    """
    Open and return a psycopg v3 connection to the Lakebase database.

    The connection URL is resolved via ``get_lakebase_url()`` at call time.
    Use the connection as a context manager to manage transactions::

        with connect() as conn:          # commits on clean exit, rolls back on error
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM cases")
                print(cur.fetchone())

    Returns
    -------
    psycopg.Connection
        An open connection with autocommit disabled (psycopg v3 default).
        The context-manager ``__exit__`` commits on success, rolls back on
        error, and closes the connection in both cases.
    """
    return psycopg.connect(get_lakebase_url())
