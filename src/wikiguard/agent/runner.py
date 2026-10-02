"""
Agent runner — the loop that calls the model, executes tools, and returns
results to the caller (Streamlit app or test notebook).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from databricks_openai import DatabricksOpenAI

from wikiguard.agent.audit import _session_var, new_session
from wikiguard.agent.prompt import SYSTEM_PROMPT
from wikiguard.agent.registry import call_tool, tool_specs
from wikiguard.config import CONFIG

log = logging.getLogger(__name__)

_TOOL_RESULT_LIMIT = 8_000

# Tools that mutate state — used to populate is_write in the return dict.
_WRITE_TOOLS = frozenset({
    "assign_case",
    "update_case_status",
    "add_case_note",
    "create_watchlist",
    "bulk_dismiss",
})


def _truncate(text: str, limit: int = _TOOL_RESULT_LIMIT) -> str:
    if len(text) > limit:
        return text[:limit] + "\u2026 [truncated]"
    return text


def _content_text(content) -> str:
    """Extract plain text from a model message's content field.

    Claude endpoints may return a list of content blocks instead of a bare
    string.  This normalises all three forms the SDK can produce:
    - str  → return as-is
    - list → join the ``text`` of every block whose type is ``"text"``;
             blocks may be dicts or objects with ``.type``/``.text`` attrs
    - None → empty string
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            else:
                # SDK object with .type / .text attributes
                if getattr(block, "type", None) == "text":
                    parts.append(getattr(block, "text", ""))
        return "".join(parts)
    return str(content)


def run_agent(
    messages: list[dict],
    session_id: Optional[str] = None,
    max_steps: int = 10,
) -> dict:
    """
    Run the agent loop for one conversational turn.

    Parameters
    ----------
    messages:
        Conversation history **without** the system prompt.  The caller owns
        this list and it is not mutated — the returned ``messages`` is the
        updated copy ready to pass back on the next turn.
    session_id:
        Audit session id shared across all tool calls in this conversation.
        A new UUID is generated if not provided.
    max_steps:
        Maximum number of model calls before giving up.

    Returns
    -------
    dict
        ``reply``      — final text for the user
        ``messages``   — updated history (no system prompt), including all
                         assistant + tool messages from this turn
        ``tool_calls`` — list of {name, arguments, ok, is_write} for this turn
        ``session_id`` — the session id used
    """
    # Set the audit session for every tool call in this turn.
    sid = session_id or new_session()
    _session_var.set(sid)

    history = list(messages)           # shallow copy; caller's list unchanged
    this_turn_calls: list[dict] = []
    specs = tool_specs()
    client = DatabricksOpenAI()

    for _ in range(max_steps):
        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

        try:
            resp = client.chat.completions.create(
                model=CONFIG.agent_model,
                messages=full_messages,
                tools=specs,
            )
        except Exception as exc:
            log.exception("Model endpoint call failed")
            return {
                "reply": f"The assistant is temporarily unavailable. ({exc})",
                "messages": history,
                "tool_calls": this_turn_calls,
                "session_id": sid,
            }

        choice = resp.choices[0]
        msg = choice.message

        if msg.tool_calls:
            # ---- tool-calling turn ----
            history.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments

                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError as exc:
                    args = {}
                    result = {"ok": False, "error": f"Invalid JSON arguments: {exc}"}
                else:
                    result = call_tool(name, args)

                this_turn_calls.append({
                    "name": name,
                    "arguments": args,
                    "ok": bool(result.get("ok", True)),
                    "is_write": name in _WRITE_TOOLS,
                })

                result_text = json.dumps(result, default=str, ensure_ascii=False)
                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _truncate(result_text),
                })

        else:
            # ---- final answer — append to history so the next turn sees it ----
            history.append({"role": "assistant", "content": msg.content})
            return {
                "reply": _content_text(msg.content).strip(),
                "messages": history,
                "tool_calls": this_turn_calls,
                "session_id": sid,
            }

    # max_steps exhausted
    return {
        "reply": (
            "I\u2019m sorry \u2014 this request needed too many steps to resolve. "
            "Please try a more specific question."
        ),
        "messages": history,
        "tool_calls": this_turn_calls,
        "session_id": sid,
    }
