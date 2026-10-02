"""
System check page — verifies connectivity and configuration after a fresh deploy.

Each check runs inside try/except so one failure never blocks the others.
Results are cached in session_state (key ``_wg_check_results``) across reruns
until the user clicks Re-run.  Secret values are never displayed.
"""
from __future__ import annotations

import os

import streamlit as st

st.title("🔧 System check")

# Map of env-var name → resource key in app.yaml
_VARS: dict[str, str] = {
    "WIKIGUARD_CONTACT_EMAIL": "contact-email",
    "WIKIGUARD_LAKEBASE_URL":  "lakebase-url",
    "WIKIGUARD_SQL_WAREHOUSE_ID": "sql-warehouse",
}


def _fmt_count(rows: list[dict], col: str = "n") -> str:
    """Format a COUNT(*) result from run_sql as a human-readable integer string."""
    if not rows:
        return "0"
    raw = rows[0].get(col, "?")
    try:
        return f"{int(raw):,}"
    except (ValueError, TypeError):
        return str(raw)


def _run_checks() -> tuple[list, str | None]:
    """
    Execute all checks and return (results, app_id).

    Each entry in results is a 4-tuple:
        (check_name: str, ok: bool, detail_markdown: str, hint: str | None)

    app_id is the service-principal application ID string if the identity
    check succeeded, else None.  The caller shows it prominently because
    it is what Unity Catalog GRANT statements target.
    """
    results: list[tuple[str, bool, str, str | None]] = []
    app_id: str | None = None

    # ------------------------------------------------------------------ #
    # 1. App identity                                                      #
    # ------------------------------------------------------------------ #
    try:
        from databricks.sdk import WorkspaceClient
        me = WorkspaceClient().current_user.me()
        display_name = me.display_name or "(no display name)"
        # For a service principal, user_name is its application ID/client ID.
        # Fall back to DATABRICKS_CLIENT_ID which Databricks Apps sets automatically.
        app_id = me.user_name or os.environ.get("DATABRICKS_CLIENT_ID") or None
        detail = f"Signed in as **{display_name}**"
        if app_id:
            detail += f" — application ID `{app_id}`"
        results.append(("App identity", True, detail, None))
    except Exception as exc:
        results.append((
            "App identity", False, str(exc),
            "Check that the app service principal has workspace access.",
        ))

    # ------------------------------------------------------------------ #
    # 2. Environment variables (set / missing only — never the values)    #
    # ------------------------------------------------------------------ #
    missing = [k for k in _VARS if not os.environ.get(k)]
    if missing:
        lines = ["The following variables are **not set**:"]
        for k in missing:
            lines.append(f"- `{k}` → resource key `{_VARS[k]}`")
        results.append((
            "Environment", False, "\n".join(lines),
            "Add the missing resource in the App UI and redeploy.",
        ))
    else:
        set_list = ", ".join(f"`{k}`" for k in _VARS)
        results.append(("Environment", True, f"All three variables are set: {set_list}.", None))

    # ------------------------------------------------------------------ #
    # 3. Lakebase                                                          #
    # ------------------------------------------------------------------ #
    try:
        from wikiguard.lakebase.client import connect
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM cases")
                row = cur.fetchone()
        results.append(("Lakebase", True, f"Connected. `cases` has {row[0]:,} rows.", None))
    except Exception as exc:
        results.append(("Lakebase", False, str(exc), "Check the `lakebase-url` resource."))

    # ------------------------------------------------------------------ #
    # 4–6. Warehouse checks                                               #
    # ------------------------------------------------------------------ #
    _warehouse_checks: list[tuple[str, str, str]] = [
        (
            "Warehouse: silver",
            "bootcamp_students.wikiguard_silver.edit_diffs",
            "Grant `USE SCHEMA` and `SELECT` on `wikiguard_silver` to the app service principal.",
        ),
        (
            "Warehouse: gold",
            "bootcamp_students.wikiguard_gold.agg_daily_triage",
            "Grant `USE SCHEMA` and `SELECT` on `wikiguard_gold` to the app service principal.",
        ),
        (
            "Warehouse: bronze",
            "bootcamp_students.wikiguard_bronze.lb_cases_history",
            "Grant `USE SCHEMA` and `SELECT` on `wikiguard_bronze` to the app service principal.",
        ),
    ]
    for check_name, table, hint in _warehouse_checks:
        try:
            from wikiguard.agent.sql import run_sql
            rows = run_sql(f"SELECT count(*) AS n FROM {table}")
            count = _fmt_count(rows)
            results.append((check_name, True, f"`{table}` has {count} rows.", None))
        except Exception as exc:
            results.append((check_name, False, str(exc), hint))

    # ------------------------------------------------------------------ #
    # 7. Model endpoint                                                    #
    # ------------------------------------------------------------------ #
    try:
        from databricks_openai import DatabricksOpenAI
        from wikiguard.config import CONFIG
        client = DatabricksOpenAI()
        resp = client.chat.completions.create(
            model=CONFIG.agent_model,
            messages=[{"role": "user", "content": "Reply with one word: ready"}],
        )
        reply = (resp.choices[0].message.content or "").strip()[:80]
        results.append((
            "Model endpoint", True,
            f"Endpoint `{CONFIG.agent_model}` responded: *{reply}*",
            None,
        ))
    except Exception as exc:
        results.append((
            "Model endpoint", False, str(exc),
            "Add the serving endpoint as a resource with **Can Query** permission and redeploy.",
        ))

    # ------------------------------------------------------------------ #
    # 8. MediaWiki API                                                     #
    # ------------------------------------------------------------------ #
    try:
        from wikiguard.common.http import session_with_retries
        from wikiguard.config import CONFIG
        session = session_with_retries(CONFIG.contact_email)
        resp = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "meta": "siteinfo", "format": "json"},
            timeout=(10.0, 30.0),
        )
        resp.raise_for_status()
        data = resp.json()
        sitename = data.get("query", {}).get("general", {}).get("sitename", "Wikipedia")
        results.append(("MediaWiki API", True, f"Reachable. Site: *{sitename}*.", None))
    except Exception as exc:
        results.append((
            "MediaWiki API", False, str(exc),
            "Check the `contact-email` resource — Wikimedia blocks requests "
            "with a missing or invalid User-Agent.",
        ))

    return results, app_id


# ------------------------------------------------------------------ #
# Page layout                                                          #
# ------------------------------------------------------------------ #

if st.button("🔄 Re-run checks", type="primary"):
    st.session_state.pop("_wg_check_results", None)

if "_wg_check_results" not in st.session_state:
    with st.spinner("Running checks …"):
        st.session_state["_wg_check_results"] = _run_checks()

results, app_id = st.session_state["_wg_check_results"]

# Application ID banner — shown prominently because Unity Catalog GRANT
# statements must target this ID (not the display name or email).
if app_id:
    st.success(
        f"**Application ID: {app_id}**  \n"
        "Use this ID when granting Unity Catalog privileges to the app service principal — "
        "for example: `GRANT USE SCHEMA ON SCHEMA wikiguard_silver TO \u2039application-id\u203a`."
    )
else:
    _id_check = next((r for r in results if r[0] == "App identity"), None)
    if _id_check and not _id_check[1]:
        st.warning("Application ID unavailable — identity check failed (see below).")

st.divider()

for check_name, ok, detail, hint in results:
    icon = "✅" if ok else "❌"
    with st.expander(f"{icon}  {check_name}", expanded=not ok):
        st.markdown(detail)
        if hint:
            st.caption(f"Hint: {hint}")
