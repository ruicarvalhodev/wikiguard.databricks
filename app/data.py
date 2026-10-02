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
