"""
SQL warehouse access and query execution.

``get_sql_warehouse_id()`` reads the warehouse ID from secrets at call time
(not at import), following the same pattern as ``get_lakebase_url()``.

``run_sql()`` executes statements via the Statement Execution API.
Works from notebooks (as the user) and from the Databricks App (as the app's
service principal) — both use ``WorkspaceClient()`` with default auth.
Uses named parameters (:name) for every user-supplied value.
"""
from __future__ import annotations

import os
from typing import Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem, StatementState


def get_sql_warehouse_id() -> str:
    """
    Load the SQL warehouse ID.

    On Databricks (``DATABRICKS_RUNTIME_VERSION`` is set): reads the secret
    ``wikiguard/sql_warehouse_id`` via ``dbutils``.  Raises ``RuntimeError``
    with a helpful message if the secret is missing.

    Locally: reads ``WIKIGUARD_SQL_WAREHOUSE_ID`` env var.
    """
    if wid := os.environ.get("WIKIGUARD_SQL_WAREHOUSE_ID"):
        return wid

    if os.environ.get("DATABRICKS_RUNTIME_VERSION"):
        try:
            from databricks.sdk.runtime import dbutils  # noqa: PLC0415
            return dbutils.secrets.get("wikiguard", "sql_warehouse_id")
        except Exception as exc:
            raise RuntimeError(
                "Could not read secret 'wikiguard/sql_warehouse_id'.  "
                "Run notebooks/03_create_secrets to set it up."
            ) from exc

    # Inside a Databricks App, a missing env var is always a misconfiguration.
    if os.environ.get("DATABRICKS_APP_NAME"):
        raise RuntimeError(
            "WIKIGUARD_SQL_WAREHOUSE_ID is not set.  "
            "Add a SQL warehouse resource with key 'sql-warehouse' to the app "
            "and map it to WIKIGUARD_SQL_WAREHOUSE_ID in app.yaml."
        )

    raise RuntimeError(
        "WIKIGUARD_SQL_WAREHOUSE_ID env var is not set and not running on "
        "Databricks.  Export the env var or run notebooks/03_create_secrets."
    )


def run_sql(statement: str, parameters: Optional[dict] = None) -> list[dict]:
    """
    Execute a SQL statement on the configured warehouse and return rows as dicts.

    Parameters
    ----------
    statement:
        SQL text using ``:name`` placeholders for user-supplied values.
    parameters:
        Mapping of parameter name → value (converted to string for the API).

    Returns
    -------
    list[dict]
        One dict per row, keyed by column name.  Empty list for DDL/DML
        statements that return no rows.

    Raises
    ------
    RuntimeError
        If the statement fails or the warehouse is not configured.
    """
    wid = get_sql_warehouse_id()

    params = [
        StatementParameterListItem(name=name, value=str(value))
        for name, value in (parameters or {}).items()
    ]

    resp = WorkspaceClient().statement_execution.execute_statement(
        statement=statement,
        warehouse_id=wid,
        parameters=params or None,
        wait_timeout="50s",
    )

    if resp.status.state == StatementState.SUCCEEDED:
        if not resp.manifest or not resp.result or not resp.result.data_array:
            return []
        cols = [c.name for c in resp.manifest.schema.columns]
        return [
            {cols[i]: row[i] for i in range(len(cols))}
            for row in resp.result.data_array
        ]

    raise RuntimeError(
        f"SQL statement failed: state={resp.status.state} "
        f"error={getattr(resp.status, 'error', None)}"
    )
