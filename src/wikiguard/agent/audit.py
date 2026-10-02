"""
Agent action auditing.

Provides a session-id context, a ``json_safe`` serializer, and an
``@audited`` decorator that logs every tool call to the ``agent_actions``
table in Lakebase.

If logging itself fails, the tool's result is still returned to the caller.
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import json
import logging
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from wikiguard.lakebase.client import connect

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session id
# ---------------------------------------------------------------------------
_session_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agent_session_id", default=str(uuid.uuid4())
)


def new_session() -> str:
    """Start a new agent session and return the session id (a UUID string)."""
    sid = str(uuid.uuid4())
    _session_var.set(sid)
    return sid


def get_session_id() -> str:
    """Return the current session id."""
    return _session_var.get()


# ---------------------------------------------------------------------------
# JSON-safe serialiser
# ---------------------------------------------------------------------------

def json_safe(obj: Any) -> Any:
    """Recursively convert non-JSON-serialisable types to plain values."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


_MAX_LOG_BYTES = 4000


def _truncate_for_log(obj: Any) -> str:
    """Serialise for the audit log.

    If the serialised form exceeds ``_MAX_LOG_BYTES``, store a valid summary
    object instead of a truncated string so the column always contains
    parseable JSON.
    """
    text = json.dumps(json_safe(obj), default=str, ensure_ascii=False)
    if len(text.encode()) > _MAX_LOG_BYTES:
        preview = text[:200]
        return json.dumps({"truncated": True, "size": len(text), "preview": preview})
    return text


# ---------------------------------------------------------------------------
# @audited decorator
# ---------------------------------------------------------------------------

def audited(is_write: bool) -> Callable:
    """
    Decorator that logs a tool call to ``agent_actions``.

    Measures wall-clock latency, serialises tool_input and tool_output as
    JSONB, and inserts one row.  If the logging insert fails, the tool's
    result is still returned — audit logging must never break a tool.
    """

    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                tool_input = dict(bound.arguments)
            except TypeError as exc:
                return {"ok": False, "error": str(exc)}

            start = time.perf_counter()
            try:
                result = func(*bound.args, **bound.kwargs)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            latency_ms = int((time.perf_counter() - start) * 1000)

            try:
                with connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO agent_actions
                                (session_id, tool_name, tool_input,
                                 tool_output, is_write, latency_ms)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                uuid.UUID(get_session_id()),
                                func.__name__,
                                _truncate_for_log(tool_input),
                                _truncate_for_log(result),
                                is_write,
                                latency_ms,
                            ),
                        )
            except Exception as _audit_exc:
                log.warning("audit log failed: %s", _audit_exc)

            return result

        return wrapper

    return decorator
