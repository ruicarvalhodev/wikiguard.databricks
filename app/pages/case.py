"""
Case detail page — inspect a case, view history, and take actions.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from data import (
    reviewers,
    ui_add_case_note,
    ui_assign_case,
    ui_find_similar_cases,
    ui_get_case_detail,
    ui_get_editor_history,
    ui_get_page_context,
    ui_update_case_status,
)


def _fmt_risk(score: float) -> str:
    """Format a revert risk score as a percentage, never rounding < 1.0 up to 100%."""
    pct = float(score) * 100
    return "100.0%" if pct >= 100.0 else f"{min(pct, 99.9):.1f}%"


st.title("Case detail")

# ------------------------------------------------------------------ #
# Pending action result (shown after a rerun triggered by a write)    #
# ------------------------------------------------------------------ #
if "_wg_action_result" in st.session_state:
    msg, ok = st.session_state.pop("_wg_action_result")
    if ok:
        st.success(msg)
    else:
        st.error(msg)

# ------------------------------------------------------------------ #
# Case ID — from queue navigation or manual entry                     #
# ------------------------------------------------------------------ #
_init_id = st.session_state.get("_wg_case_id")

if _init_id is None:
    st.info(
        "No case selected. Go to the **Queue** to pick one, "
        "or enter a case ID below."
    )
    col_id, _ = st.columns([2, 5])
    with col_id:
        _manual = st.number_input("Case ID", min_value=1, step=1, value=1)
    if st.button("Open"):
        st.session_state["_wg_case_id"] = int(_manual)
        st.rerun()
    st.stop()

col_id, _ = st.columns([2, 5])
with col_id:
    _new_id = st.number_input(
        "Case ID", min_value=1, step=1,
        value=int(_init_id),
    )
if int(_new_id) != int(_init_id):
    st.session_state["_wg_case_id"] = int(_new_id)
    st.rerun()

case_id = int(_init_id)

# ------------------------------------------------------------------ #
# Load case detail                                                     #
# ------------------------------------------------------------------ #
with st.spinner("Loading case…"):
    result = ui_get_case_detail(case_id)

if not result.get("ok"):
    st.error(result.get("error", "Case not found."))
    st.stop()

case  = result["case"]
notes = result["notes"]
diff  = result["diff"]

# ------------------------------------------------------------------ #
# Header                                                               #
# ------------------------------------------------------------------ #
st.header(f"#{case['case_id']} — {case['page_title']}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Status",   case["status"])
c2.metric("Risk",     _fmt_risk(case["revert_risk"]))
c3.metric("Tier",     case["tier"])
c4.metric("Assignee", case.get("assignee") or "—")

_delta = int(case["byte_delta"])
st.markdown(
    f"**Wiki:** `{case['wiki']}`&nbsp;&nbsp;|  "
    f"**Editor:** `{case['editor']}`&nbsp;&nbsp;|  "
    f"**Byte change:** `{_delta:+,}`&nbsp;&nbsp;|  "
    f"[View diff ↗]({case['diff_url']})"
)

st.divider()

# ------------------------------------------------------------------ #
# Changes (diff text)                                                  #
# ------------------------------------------------------------------ #
st.subheader("Changes")
if diff.get("available"):
    col_rm, col_add = st.columns(2)
    with col_rm:
        st.markdown("**Removed**")
        st.code(diff.get("removed_text") or "(empty)", language=None)
    with col_add:
        st.markdown("**Added**")
        st.code(diff.get("added_text") or "(empty)", language=None)
elif "note" in diff:
    st.info(f"{diff['note']}  →  [View diff ↗]({case['diff_url']})")
elif "error" in diff:
    st.warning(diff["error"])
else:
    st.info(f"Diff text unavailable.  →  [View diff ↗]({case['diff_url']})")

# ------------------------------------------------------------------ #
# Page history                                                         #
# ------------------------------------------------------------------ #
st.subheader("Page history")
with st.spinner("Loading page history…"):
    page_ctx = ui_get_page_context(case["wiki"], case["page_title"])

if page_ctx.get("ok"):
    revs = page_ctx.get("last_revisions", [])
    if revs:
        df_rev   = pd.DataFrame(revs)
        show_col = [c for c in ["user", "timestamp", "size", "comment"] if c in df_rev.columns]
        st.dataframe(
            df_rev[show_col] if show_col else df_rev,
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("No recent revisions.")
else:
    st.caption(page_ctx.get("error", "Could not load page history."))

# ------------------------------------------------------------------ #
# Editor history (expander)                                            #
# ------------------------------------------------------------------ #
with st.expander("Editor history"):
    eh = ui_get_editor_history(case["editor"])
    if eh.get("ok"):
        st.caption(f"Open cases for `{case['editor']}`: {eh.get('open_cases', 0)}")
        edits = eh.get("recent_edits", [])
        if edits:
            st.dataframe(pd.DataFrame(edits), use_container_width=True, hide_index=True)
        else:
            st.caption("No recent edits found.")
    else:
        st.caption(eh.get("error", "Could not load editor history."))

# ------------------------------------------------------------------ #
# Similar cases (expander)                                             #
# ------------------------------------------------------------------ #
with st.expander("Similar cases"):
    sim = ui_find_similar_cases(case_id)
    if not sim.get("ok", True):
        st.caption(sim.get("error", "Could not load similar cases."))
    elif sim.get("available") is False:
        st.caption(sim.get("note", "Similarity search not available for this case."))
    else:
        items = sim.get("similar", [])
        if items:
            st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)
        else:
            st.caption("No similar cases found.")

st.divider()

# ------------------------------------------------------------------ #
# Notes                                                                #
# ------------------------------------------------------------------ #
st.subheader(f"Notes ({len(notes)})")
if not notes:
    st.caption("No notes yet.")
else:
    for n in notes:
        if n["author_type"] == "human":
            author = n.get("author_name") or "Human"
            badge  = "👤"
        else:
            author = "Agent"
            badge  = "🤖"
        st.markdown(f"{badge} **{author}** · *{n['created_at']}*")
        st.markdown(f"> {n['body']}")
        st.divider()

# ------------------------------------------------------------------ #
# Actions                                                              #
# ------------------------------------------------------------------ #
st.subheader("Actions")

_ALL_STATUSES  = ["open", "in_review", "escalated", "resolved", "dismissed"]
_CONSEQUENTIAL = {"escalated", "resolved", "dismissed"}

tab_status, tab_assign, tab_note = st.tabs(
    ["🔄 Change status", "👤 Assign", "📝 Add note"]
)

# ---- Change status ----
with tab_status:
    with st.form("form_change_status"):
        new_status = st.selectbox("New status", _ALL_STATUSES)
        reason     = st.text_area("Reason (required)", height=80)
        if new_status in _CONSEQUENTIAL:
            confirmed = st.checkbox(
                f"I confirm changing status to **{new_status}** — this action is consequential."
            )
        else:
            confirmed = True
        submitted_status = st.form_submit_button("Change status")

    if submitted_status:
        if not reason.strip():
            st.warning("Reason is required.")
        elif not confirmed:
            st.warning(f"Tick the confirmation box before setting status to \u2018{new_status}\u2019.")
        else:
            r = ui_update_case_status(case_id, new_status, reason.strip())
            if r.get("ok"):
                resolved_note = ""
                if r["after"].get("resolved_at"):
                    resolved_note = f" (resolved at {r['after']['resolved_at']})"
                st.session_state["_wg_action_result"] = (
                    f"Status changed: **{r['before']['status']}** → **{r['after']['status']}**"
                    + resolved_note,
                    True,
                )
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(r.get("error", "Unknown error."))

# ---- Assign ----
with tab_assign:
    with st.form("form_assign"):
        rev_opts = [(None, "— select reviewer —")] + list(reviewers())
        sel_rev  = st.selectbox("Assign to", rev_opts, format_func=lambda x: x[1])
        submitted_assign = st.form_submit_button("Assign")

    if submitted_assign:
        if not sel_rev[0]:
            st.warning("Select a reviewer.")
        else:
            r = ui_assign_case(case_id, str(sel_rev[0]))
            if r.get("ok"):
                after = r["after"]
                st.session_state["_wg_action_result"] = (
                    f"Assigned to **{after.get('assignee', after.get('assigned_to'))}** "
                    f"— status: {after.get('status')}",
                    True,
                )
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(r.get("error", "Unknown error."))

# ---- Add note ----
with tab_note:
    with st.form("form_add_note"):
        note_body = st.text_area("Note", height=100)
        submitted_note = st.form_submit_button("Add note")

    if submitted_note:
        if not note_body.strip():
            st.warning("Note body is required.")
        else:
            r = ui_add_case_note(case_id, note_body.strip())
            if r.get("ok"):
                st.session_state["_wg_action_result"] = (
                    f"Note #{r['note_id']} added at {r['created_at']}.",
                    True,
                )
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(r.get("error", "Unknown error."))

# ------------------------------------------------------------------ #
# Ask the agent                                                        #
# ------------------------------------------------------------------ #
st.divider()
if st.button(
    "\U0001f4ac Ask the agent about this case",
    use_container_width=True,
    key="btn_ask_agent",
):
    st.session_state["_wg_chat_prefill"] = (
        f"Tell me about case {case_id}: what happened, "
        "has it been reverted, and what do you recommend?"
    )
    st.switch_page("pages/chat.py")
