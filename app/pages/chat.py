"""
Agent chat page — shows the agent working: which tools it calls, live.
"""
from __future__ import annotations

import streamlit as st

from wikiguard.agent.audit import new_session
from wikiguard.agent.runner import run_agent

# ------------------------------------------------------------------ #
# Session state keys                                                   #
# ------------------------------------------------------------------ #
_MSGS       = "_wg_chat_messages"    # agent history (no system prompt)
_TRANSCRIPT = "_wg_chat_transcript"  # list of {role, text, tool_calls}
_SID        = "_wg_chat_session_id"

_STARTERS = [
    "What's at the top of the queue?",
    "Are several open cases coming from the same editor?",
    "Show me open tier B cases with risk below 0.2",
    "How is the queue doing today?",
]


def _new_conversation() -> None:
    st.session_state[_MSGS]       = []
    st.session_state[_TRANSCRIPT] = []
    st.session_state[_SID]        = new_session()


def _reviewer_name() -> str | None:
    """Return the display name of the 'Acting as' reviewer, or None."""
    rid = st.session_state.get("_wg_reviewer_id")
    if not rid:
        return None
    try:
        from data import reviewers  # noqa: PLC0415
        return {r[0]: r[1] for r in reviewers()}.get(rid)
    except Exception:
        return None


def _render_tool_calls(tool_calls: list[dict]) -> None:
    """Render a collapsed expander listing the tool calls for one turn."""
    if not tool_calls:
        return
    with st.expander(f"Tools used ({len(tool_calls)})", expanded=False):
        for tc in tool_calls:
            icon    = "\u270f\ufe0f" if tc["is_write"] else "\U0001f50d"
            ok_icon = "\u2705" if tc["ok"] else "\u274c"
            key_args = {k: v for k, v in list(tc["arguments"].items())[:2]}
            args_str = ", ".join(f"{k}={v!r}" for k, v in key_args.items())
            st.markdown(f"{icon} `{tc['name']}({args_str})` {ok_icon}")


# ------------------------------------------------------------------ #
# Initialise state on first visit                                     #
# ------------------------------------------------------------------ #
if _MSGS not in st.session_state:
    _new_conversation()

# ------------------------------------------------------------------ #
# Header + new-conversation button                                     #
# ------------------------------------------------------------------ #
head_col, btn_col = st.columns([6, 1])
with head_col:
    st.title("\U0001f4ac Chat")
with btn_col:
    st.write("")
    if st.button("\U0001f504 New", key="btn_new_conv", help="Clear history and start fresh"):
        _new_conversation()
        st.rerun()

# ------------------------------------------------------------------ #
# Render existing transcript                                           #
# ------------------------------------------------------------------ #
for entry in st.session_state[_TRANSCRIPT]:
    with st.chat_message(entry["role"]):
        st.markdown(entry["text"])
        if entry["role"] == "assistant":
            _render_tool_calls(entry.get("tool_calls", []))

# ------------------------------------------------------------------ #
# Auto-send a prefilled message (e.g. from the Case page)             #
# ------------------------------------------------------------------ #
_prefill = st.session_state.pop("_wg_chat_prefill", None)

# ------------------------------------------------------------------ #
# Starter prompts (shown only when the transcript is empty)           #
# ------------------------------------------------------------------ #
if not st.session_state[_TRANSCRIPT] and not _prefill:
    st.markdown("**Try asking:**")
    c1, c2 = st.columns(2)
    for i, starter in enumerate(_STARTERS):
        with (c1 if i % 2 == 0 else c2):
            if st.button(starter, key=f"starter_{i}", use_container_width=True):
                _prefill = starter

# ------------------------------------------------------------------ #
# Chat input                                                          #
# ------------------------------------------------------------------ #
prompt = st.chat_input("Ask the agent\u2026") or _prefill

if prompt:
    # Show user message immediately
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state[_TRANSCRIPT].append({"role": "user", "text": prompt, "tool_calls": []})
    st.session_state[_MSGS].append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        with st.status("Working\u2026", expanded=True) as _status:
            def _on_tool_call(tc: dict) -> None:
                icon    = "\u270f\ufe0f" if tc["is_write"] else "\U0001f50d"
                ok_icon = "\u2705" if tc["ok"] else "\u274c"
                _status.write(f"{icon} `{tc['name']}` {ok_icon}")

            agent_result = run_agent(
                st.session_state[_MSGS],
                session_id=st.session_state[_SID],
                on_tool_call=_on_tool_call,
                reviewer_name=_reviewer_name(),
            )
        _n = len(agent_result["tool_calls"])
        _done_label = (
            f"Done \u2014 {_n} tool{'s' if _n != 1 else ''}" if _n else "Done"
        )
        _status.update(label=_done_label, state="complete", expanded=False)

        st.session_state[_SID]  = agent_result["session_id"]
        st.session_state[_MSGS] = agent_result["messages"]

        reply      = agent_result["reply"]
        tool_calls = agent_result["tool_calls"]

        _AGENT_ERR = (
            "The assistant is temporarily unavailable",
            "I\u2019m sorry \u2014 this request needed too many steps",
        )
        if any(reply.startswith(p) for p in _AGENT_ERR):
            st.error(reply)
        else:
            st.markdown(reply)
        _render_tool_calls(tool_calls)

        st.session_state[_TRANSCRIPT].append({
            "role":       "assistant",
            "text":       reply,
            "tool_calls": tool_calls,
        })

        # Flush UI caches if any write tool ran this turn
        if any(tc["is_write"] for tc in tool_calls):
            st.cache_data.clear()
