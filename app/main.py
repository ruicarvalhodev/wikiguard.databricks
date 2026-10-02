"""
WikiGuard Streamlit app — entry point and navigation.

Run as:  streamlit run app/main.py   (from the repo root)

The Databricks Apps runtime sets STREAMLIT_SERVER_PORT and
STREAMLIT_SERVER_ADDRESS=0.0.0.0 automatically; no manual port wiring needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add <repo root>/src to sys.path so 'import wikiguard' works in all pages.
# This runs before any page script, so the modification is visible everywhere.
_here = Path(__file__).resolve().parent   # .../app/
_repo_root = _here.parent                 # repo root
sys.path.insert(0, str(_repo_root / "src"))
sys.path.insert(0, str(_here))           # app/ — so all pages can import data

import streamlit as st

st.set_page_config(page_title="WikiGuard", layout="wide")

# ------------------------------------------------------------------ #
# Sidebar: brand + reviewer selector                                   #
# ------------------------------------------------------------------ #
with st.sidebar:
    st.markdown("### WikiGuard — Wikipedia integrity triage")
    st.divider()
    try:
        from data import reviewers  # noqa: PLC0415
        rev_list = reviewers()
        if rev_list:
            rev_ids   = [None] + [r[0] for r in rev_list]
            rev_names = {r[0]: r[1] for r in rev_list}
            st.selectbox(
                "Acting as",
                rev_ids,
                format_func=lambda x: "— select reviewer —" if x is None else rev_names[x],
                key="_wg_reviewer_id",  # value stored directly in session state
            )
        else:
            st.caption("No reviewers found in Lakebase.")
    except Exception:
        st.caption("Reviewer list unavailable.")

# ------------------------------------------------------------------ #
# Navigation                                                           #
# ------------------------------------------------------------------ #
pg = st.navigation([
    st.Page("pages/queue.py",        title="Queue",        icon="📋"),
    st.Page("pages/case.py",         title="Case",         icon="🔍"),
    st.Page("pages/chat.py",         title="Chat",         icon="💬"),
    st.Page("pages/analytics.py",    title="Analytics",    icon="📊"),
    st.Page("pages/system_check.py", title="System check", icon="🔧"),
])
pg.run()
