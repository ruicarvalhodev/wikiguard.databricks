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

import streamlit as st

st.set_page_config(page_title="WikiGuard", layout="wide")

with st.sidebar:
    st.markdown("### WikiGuard — Wikipedia integrity triage")

pg = st.navigation([
    st.Page("pages/queue.py",        title="Queue",        icon="📋"),
    st.Page("pages/chat.py",         title="Chat",         icon="💬"),
    st.Page("pages/analytics.py",    title="Analytics",    icon="📊"),
    st.Page("pages/system_check.py", title="System check", icon="🔧"),
])
pg.run()
