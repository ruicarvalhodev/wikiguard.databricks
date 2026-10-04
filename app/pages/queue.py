"""
Review queue page — browse, filter, and navigate to cases.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from data import reviewers, ui_case_counts, ui_queue_search

st.title("📋 Queue")

# ------------------------------------------------------------------ #
# Metrics                                                              #
# ------------------------------------------------------------------ #

try:
    with st.spinner("Loading counts…"):
        counts = ui_case_counts()
except Exception:
    st.error(
        "Can\u2019t reach Lakebase \u2014 check the **System check** page for details."
    )
    st.stop()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Open",        int(counts.get("open", 0)))
m2.metric("In review",   int(counts.get("in_review", 0)))
m3.metric("Escalated",   int(counts.get("escalated", 0)))
m4.metric("Open tier A", int(counts.get("open_tier_a", 0)))

st.divider()

# ------------------------------------------------------------------ #
# Filters                                                              #
# ------------------------------------------------------------------ #

_ALL_STATUSES = ["open", "in_review", "escalated", "resolved", "dismissed"]
_ALL_TIERS    = ["A", "B", "C", "D", "N"]
_ALL_WIKIS    = ["enwiki", "dewiki", "frwiki"]

f1, f2, f3 = st.columns([3, 1, 1])
with f1:
    sel_statuses = st.multiselect("Status", _ALL_STATUSES, default=["open", "in_review"])
with f2:
    sel_tier = st.selectbox("Tier", [""] + _ALL_TIERS, format_func=lambda x: x or "All")
with f3:
    sel_wiki = st.selectbox("Wiki", [""] + _ALL_WIKIS, format_func=lambda x: x or "All")

f4, f5, f6 = st.columns([3, 2, 1])
with f4:
    risk_range = st.slider("Risk", 0.0, 1.0, (0.0, 1.0), step=0.05, format="%.2f")
with f5:
    try:
        rev_opts = [(None, "All")] + list(reviewers())
    except Exception:
        rev_opts = [(None, "All")]
    sel_assignee = st.selectbox("Assignee", rev_opts, format_func=lambda x: x[1])
with f6:
    editor_q = st.text_input("Editor")

# ------------------------------------------------------------------ #
# Query                                                                #
# ------------------------------------------------------------------ #

with st.spinner("Loading cases…"):
    result = ui_queue_search(
        statuses=sel_statuses or None,
        tier=sel_tier or None,
        wiki=sel_wiki or None,
        min_risk=risk_range[0] if risk_range[0] > 0.0 else None,
        max_risk=risk_range[1] if risk_range[1] < 1.0 else None,
        assignee_id=sel_assignee[0],
        editor=editor_q.strip() or None,
        limit=50,
    )

if not result.get("ok"):
    st.error(result.get("error", "Query failed."))
    st.stop()

cases = result.get("cases", [])

if not cases:
    st.info("No cases match the current filters — try widening the selection.")
    st.stop()

st.caption(f"Showing the top {len(cases)} cases. Select a row to open the case.")

# ------------------------------------------------------------------ #
# Table with single-row selection                                      #
# ------------------------------------------------------------------ #

df = pd.DataFrame(cases)
if "diff_url" not in df.columns:
    df["diff_url"] = None

event = st.dataframe(
    df[["case_id", "tier", "revert_risk", "wiki", "page_title", "editor",
        "byte_delta", "status", "assignee", "diff_url"]],
    column_config={
        "case_id":     st.column_config.NumberColumn("ID",      width="small"),
        "tier":        st.column_config.TextColumn("Tier",      width="small"),
        "revert_risk": st.column_config.ProgressColumn(
            "Risk", min_value=0, max_value=1, format="%.2f"
        ),
        "wiki":        st.column_config.TextColumn("Wiki",      width="small"),
        "page_title":  st.column_config.TextColumn("Page"),
        "editor":      st.column_config.TextColumn("Editor"),
        "byte_delta":  st.column_config.NumberColumn("Bytes"),
        "status":      st.column_config.TextColumn("Status"),
        "assignee":    st.column_config.TextColumn("Assignee"),
        "diff_url":    st.column_config.LinkColumn("Diff",      display_text="View ↗"),
    },
    on_select="rerun",
    selection_mode="single-row",
    use_container_width=True,
    hide_index=True,
)

sel = event.selection.rows
if sel:
    st.session_state["_wg_case_id"] = int(df.iloc[sel[0]]["case_id"])
    st.switch_page("pages/case.py")
