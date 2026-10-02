"""
Tool registry: maps tool names to functions, generates OpenAI
function-calling specs, and dispatches tool calls.
"""
from __future__ import annotations

from wikiguard.agent.tools_read import (
    find_similar_cases,
    get_case_detail,
    get_editor_history,
    get_page_context,
    query_triage_metrics,
    search_cases,
)
from wikiguard.agent.tools_write import (
    add_case_note,
    assign_case,
    bulk_dismiss,
    create_watchlist,
    update_case_status,
)

TOOLS = {
    "search_cases": search_cases,
    "get_case_detail": get_case_detail,
    "get_editor_history": get_editor_history,
    "get_page_context": get_page_context,
    "query_triage_metrics": query_triage_metrics,
    "find_similar_cases": find_similar_cases,
    "assign_case": assign_case,
    "update_case_status": update_case_status,
    "add_case_note": add_case_note,
    "create_watchlist": create_watchlist,
    "bulk_dismiss": bulk_dismiss,
}


def tool_specs() -> list[dict]:
    """Return tool definitions in OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": "search_cases",
                "description": (
                    "Search the review queue with optional filters. Returns cases in "
                    "priority order (priority DESC, revert_risk DESC, event_ts DESC). "
                    "Call with no arguments to see the top of the queue."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["open", "in_review", "escalated", "resolved", "dismissed"]},
                        "wiki": {"type": "string"},
                        "tier": {"type": "string", "enum": ["A", "B", "C", "D", "N"]},
                        "min_risk": {"type": "number"},
                        "editor": {"type": "string"},
                        "assigned_to": {"type": "string", "description": "Reviewer name or ID"},
                        "limit": {"type": "integer", "default": 10, "maximum": 50},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_case_detail",
                "description": (
                    "Get full details for a single case: the case itself, its notes "
                    "(newest first), and the first ~1,500 characters of the added "
                    "and removed diff text from Delta."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"case_id": {"type": "integer"}},
                    "required": ["case_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_editor_history",
                "description": (
                    "Get an editor's recent edits across all wikis (from Delta) "
                    "and how many of their edits are currently open cases in Lakebase."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "editor": {"type": "string"},
                        "limit": {"type": "integer", "default": 20, "maximum": 50},
                    },
                    "required": ["editor"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_page_context",
                "description": (
                    "Get the protection level and the last 5 revisions of a "
                    "Wikipedia page from the MediaWiki API. Derives the language "
                    "from the wiki identifier (enwiki → en)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "wiki": {"type": "string", "description": "Wiki identifier (e.g. enwiki)"},
                        "page_title": {"type": "string"},
                    },
                    "required": ["wiki", "page_title"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_triage_metrics",
                "description": (
                    "Get queue-wide metrics: counts by status and by tier, open "
                    "cases per reviewer, and the age of the oldest open case."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_similar_cases",
                "description": (
                    "Find the k most similar edits to a case by cosine similarity "
                    "of diff embeddings stored in Delta. Returns similar edits with "
                    "similarity score, page, editor, and the Lakebase case_id if "
                    "the similar edit is in the queue."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "integer"},
                        "k": {"type": "integer", "default": 5, "maximum": 20},
                    },
                    "required": ["case_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "assign_case",
                "description": (
                    "Assign a case to a reviewer by name (case-insensitive) or ID. "
                    "If the case is open, it also moves to in_review. Errors if the "
                    "reviewer is not found or is ambiguous."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "integer"},
                        "reviewer": {"type": "string", "description": "Reviewer name or ID"},
                    },
                    "required": ["case_id", "reviewer"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_case_status",
                "description": (
                    "Update a case's status and add the reason as an agent note in "
                    "the same transaction. Errors if the case already has that status."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "integer"},
                        "status": {"type": "string", "enum": ["open", "in_review", "escalated", "resolved", "dismissed"]},
                        "reason": {"type": "string", "description": "Required, non-empty reason for the status change"},
                    },
                    "required": ["case_id", "status", "reason"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_case_note",
                "description": "Add a note to a case (author_type = 'agent').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "integer"},
                        "body": {"type": "string", "description": "Non-empty note text"},
                    },
                    "required": ["case_id", "body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_watchlist",
                "description": "Create a watchlist for a reviewer with a set of pages to monitor.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reviewer": {"type": "string", "description": "Reviewer name or ID"},
                        "name": {"type": "string"},
                        "pages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"wiki": {"type": "string"}, "page_title": {"type": "string"}},
                                "required": ["wiki", "page_title"],
                            },
                        },
                    },
                    "required": ["reviewer", "name", "pages"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "bulk_dismiss",
                "description": (
                    "Dismiss up to 50 cases at once. MUST be called with confirm=false "
                    "first to get a preview of which cases would change. Only call "
                    "with confirm=true after the user agrees. Adds the reason as an "
                    "agent note on each dismissed case. Skips cases already dismissed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "case_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 50},
                        "reason": {"type": "string"},
                        "confirm": {"type": "boolean", "default": False},
                    },
                    "required": ["case_ids", "reason"],
                },
            },
        },
    ]


def call_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tool by name with the given arguments dict."""
    func = TOOLS.get(name)
    if func is None:
        return {"ok": False, "error": f"Unknown tool '{name}'. Available: {sorted(TOOLS.keys())}."}
    try:
        return func(**arguments)
    except TypeError as exc:
        return {"ok": False, "error": f"Bad arguments for '{name}': {exc}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
