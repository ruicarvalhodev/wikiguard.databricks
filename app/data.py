"""
app/data.py — UI-side data helpers.

All read helpers run the corresponding agent tool inside audit_disabled() so
page loads are never counted as agent activity in agent_actions.  Write helpers
also pass author_type='human' and author_id from the 'Acting as' sidebar.

Nothing here does blocking I/O at import time.
"""
from __future__ import annotations

from psycopg.rows import dict_row

import streamlit as st

from wikiguard.agent.audit import audit_disabled
from wikiguard.agent.tools_read import (
    find_similar_cases,
    get_case_detail,
    get_editor_history,
    get_page_context,
)
from wikiguard.agent.tools_write import (
    add_case_note,
    assign_case,
    update_case_status,
)
from wikiguard.lakebase.client import connect

import pandas as pd

from wikiguard.agent.sql import run_sql
from wikiguard.config import CONFIG


# ---------------------------------------------------------------------------
# Reviewer list
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def reviewers() -> list[tuple[int, str]]:
    """Return (reviewer_id, display_name) pairs from Lakebase, cached 5 min."""
    with connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT reviewer_id, display_name FROM reviewers ORDER BY display_name"
            )
            rows = cur.fetchall()
    return [(r["reviewer_id"], r["display_name"]) for r in rows]


def _acting_as() -> int | None:
    """Reviewer ID selected in the ‘Acting as’ sidebar, or None."""
    return st.session_state.get("_wg_reviewer_id")


# ---------------------------------------------------------------------------
# Case counts (metrics row in the queue page)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def ui_case_counts() -> dict:
    """Case counts by status plus open tier-A count, cached 30 s."""
    with connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'open')       AS open,
                    COUNT(*) FILTER (WHERE status = 'in_review')  AS in_review,
                    COUNT(*) FILTER (WHERE status = 'escalated')  AS escalated,
                    COUNT(*) FILTER (WHERE status = 'open' AND tier = 'A') AS open_tier_a
                FROM cases
                """
            )
            row = cur.fetchone()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Queue search — direct query, multi-status, no audit
# ---------------------------------------------------------------------------

def ui_queue_search(
    statuses: list[str] | None = None,
    tier: str | None = None,
    wiki: str | None = None,
    min_risk: float | None = None,
    max_risk: float | None = None,
    assignee_id: int | None = None,
    editor: str | None = None,
    limit: int = 50,
) -> dict:
    """Search the queue with multi-status support, without audit logging."""
    try:
        where: list[str] = []
        params: list = []

        if statuses:
            where.append("c.status = ANY(%s::case_status[])")
            params.append(statuses)
        if tier:
            where.append("c.tier = %s")
            params.append(tier)
        if wiki:
            where.append("c.wiki = %s")
            params.append(wiki)
        if min_risk is not None:
            where.append("c.revert_risk >= %s")
            params.append(float(min_risk))
        if max_risk is not None:
            where.append("c.revert_risk <= %s")
            params.append(float(max_risk))
        if assignee_id is not None:
            where.append("c.assigned_to = %s")
            params.append(int(assignee_id))
        if editor:
            where.append("c.editor = %s")
            params.append(editor)

        where_clause = ("WHERE " + " AND ".join(where)) if where else ""
        n = max(1, min(int(limit), 50))
        sql = f"""
            SELECT c.case_id, c.priority, c.tier, c.revert_risk,
                   c.wiki, c.page_title, c.editor, c.byte_delta,
                   c.status, c.diff_url,
                   r.display_name AS assignee
            FROM cases c
            LEFT JOIN reviewers r ON r.reviewer_id = c.assigned_to
            {where_clause}
            ORDER BY c.priority DESC, c.revert_risk DESC, c.event_ts DESC
            LIMIT {n}
        """
        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return {"ok": True, "cases": [dict(r) for r in rows]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Read wrappers — tool called inside audit_disabled()
# ---------------------------------------------------------------------------

def ui_get_case_detail(case_id: int) -> dict:
    with audit_disabled():
        return get_case_detail(case_id)


def ui_get_page_context(wiki: str, page_title: str) -> dict:
    with audit_disabled():
        return get_page_context(wiki, page_title)


def ui_get_editor_history(editor: str, limit: int = 20) -> dict:
    with audit_disabled():
        return get_editor_history(editor, limit)


def ui_find_similar_cases(case_id: int, k: int = 5) -> dict:
    with audit_disabled():
        return find_similar_cases(case_id, k)


# ---------------------------------------------------------------------------
# Write wrappers — audit disabled, human attribution
# ---------------------------------------------------------------------------

def ui_assign_case(case_id: int, reviewer: str) -> dict:
    """Assign a case (reviewer is a reviewer_id string or display name)."""
    with audit_disabled():
        return assign_case(case_id, reviewer)


def ui_update_case_status(case_id: int, status: str, reason: str) -> dict:
    """Change status; note attributed to the active reviewer."""
    rid = _acting_as()
    if rid is None:
        st.warning("Select a reviewer in \u2018Acting as\u2019 before performing write actions.")
        return {"ok": False, "error": "No reviewer selected."}
    with audit_disabled():
        return update_case_status(
            case_id, status, reason,
            author_type="human",
            author_id=rid,
        )


def ui_add_case_note(case_id: int, body: str) -> dict:
    """Add a human note attributed to the active reviewer."""
    rid = _acting_as()
    if rid is None:
        st.warning("Select a reviewer in \u2018Acting as\u2019 before performing write actions.")
        return {"ok": False, "error": "No reviewer selected."}
    with audit_disabled():
        return add_case_note(
            case_id, body,
            author_type="human",
            author_id=rid,
        )


# ---------------------------------------------------------------------------
# Analytics queries — cached, read from Delta via SQL warehouse
# ---------------------------------------------------------------------------

_LATENCY_VIEW = f"{CONFIG.catalog}.{CONFIG.gold_schema}.vw_bronze_latency"


@st.cache_data(ttl=60)
def ui_pipeline_counts() -> pd.DataFrame:
    """Row counts for bronze, silver edits, silver candidates, and the triage queue."""
    rows = run_sql(f"""
        SELECT
            (SELECT COUNT(*) FROM {CONFIG.bronze_table})            AS bronze,
            (SELECT COUNT(*) FROM {CONFIG.silver_edits_table})      AS edits,
            (SELECT COUNT(*) FROM {CONFIG.silver_candidates_table}) AS candidates,
            (SELECT COUNT(*) FROM {CONFIG.gold_triage_table})       AS queue
    """)
    df = pd.DataFrame(rows)
    if not df.empty:
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=60)
def ui_bronze_latency() -> pd.DataFrame:
    """Per-minute p50/p95 ingest latency from vw_bronze_latency (last hour)."""
    rows = run_sql(f"""
        SELECT minute, events, p50_seconds, p95_seconds
        FROM {_LATENCY_VIEW}
        ORDER BY minute
    """)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["minute"] = pd.to_datetime(df["minute"])
        df["events"] = pd.to_numeric(df["events"], errors="coerce")
        df["p50_seconds"] = pd.to_numeric(df["p50_seconds"], errors="coerce")
        df["p95_seconds"] = pd.to_numeric(df["p95_seconds"], errors="coerce")
    return df


@st.cache_data(ttl=60)
def ui_pipeline_health() -> pd.DataFrame:
    """Latest row from pipeline_health."""
    rows = run_sql(f"""
        SELECT run_at, case_events_added, transitions_total,
               agent_actions_total, latest_cdf_ts, cdf_lag_seconds
        FROM {CONFIG.gold_pipeline_health}
        ORDER BY run_at DESC
        LIMIT 1
    """)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["run_at"] = pd.to_datetime(df["run_at"])
        df["latest_cdf_ts"] = pd.to_datetime(df["latest_cdf_ts"])
        for col in ["case_events_added", "transitions_total", "agent_actions_total", "cdf_lag_seconds"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=60)
def ui_daily_triage() -> pd.DataFrame:
    """Daily triage activity from agg_daily_triage (most recent 90 rows)."""
    rows = run_sql(f"""
        SELECT day, tier, cases_created, escalated, resolved, dismissed, median_hours_to_close
        FROM {CONFIG.gold_agg_daily_triage}
        ORDER BY day DESC
        LIMIT 90
    """)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["day"] = pd.to_datetime(df["day"])
        for col in ["cases_created", "escalated", "resolved", "dismissed"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        df["median_hours_to_close"] = pd.to_numeric(df["median_hours_to_close"], errors="coerce")
    return df


@st.cache_data(ttl=60)
def ui_case_transitions() -> pd.DataFrame:
    """Transition counts and median dwell times (non-snapshot rows only)."""
    rows = run_sql(f"""
        SELECT
            from_status,
            to_status,
            COUNT(*)                                                                  AS count,
            ROUND(PERCENTILE_APPROX(seconds_in_previous_state, 0.5) / 3600.0, 1)    AS median_hours
        FROM {CONFIG.gold_fact_case_transitions}
        WHERE is_snapshot = false
          AND from_status IS NOT NULL
          AND seconds_in_previous_state IS NOT NULL
        GROUP BY from_status, to_status
        ORDER BY count DESC
    """)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["count"] = pd.to_numeric(df["count"], errors="coerce").fillna(0).astype(int)
        df["median_hours"] = pd.to_numeric(df["median_hours"], errors="coerce")
    return df


@st.cache_data(ttl=60)
def ui_agent_daily() -> pd.DataFrame:
    """Per-day, per-tool agent activity from agg_agent_daily."""
    rows = run_sql(f"""
        SELECT event_date, tool_name, calls, writes, errors,
               error_rate, avg_latency_ms, p95_latency_ms, sessions
        FROM {CONFIG.gold_agg_agent_daily}
        ORDER BY event_date DESC
    """)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["event_date"] = pd.to_datetime(df["event_date"])
        for col in ["calls", "writes", "errors", "sessions"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        for col in ["error_rate", "avg_latency_ms", "p95_latency_ms"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df
