"""
Agent write tools — five tools that modify cases, notes, and watchlists in Lakebase.

Every tool runs in one transaction, returns what changed (before and after
values), and never raises to the caller; errors come back as
{"ok": False, "error": "<message>"}.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from wikiguard.agent.audit import audited, json_safe
from wikiguard.lakebase.client import connect

_VALID_STATUS = {"open", "in_review", "escalated", "resolved", "dismissed"}


@audited(is_write=True)
def assign_case(case_id: int, reviewer: str) -> dict:
    """
    Assign a case to a reviewer by name (case-insensitive) or ID. If the case
    is open, it also moves to in_review. Returns the before and after values.
    Errors if the reviewer is not found or is ambiguous (multiple matches).
    """
    try:
        cid = int(case_id)
        if not reviewer or not str(reviewer).strip():
            return {"ok": False, "error": "reviewer is required."}
        reviewer = str(reviewer).strip()

        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # Resolve reviewer
                if reviewer.isdigit():
                    cur.execute(
                        "SELECT reviewer_id, display_name FROM reviewers WHERE reviewer_id = %s",
                        (int(reviewer),),
                    )
                else:
                    cur.execute(
                        "SELECT reviewer_id, display_name FROM reviewers WHERE LOWER(display_name) = LOWER(%s)",
                        (reviewer,),
                    )
                matches = cur.fetchall()

                if not matches:
                    return {"ok": False, "error": f"Reviewer '{reviewer}' not found."}
                if len(matches) > 1:
                    return {"ok": False, "error": f"Reviewer '{reviewer}' is ambiguous ({len(matches)} matches). Use the reviewer ID."}

                rid, rname = matches[0]["reviewer_id"], matches[0]["display_name"]

                cur.execute(
                    """
                    SELECT case_id, status, assigned_to
                    FROM cases WHERE case_id = %s
                    """,
                    (cid,),
                )
                before = cur.fetchone()
                if not before:
                    return {"ok": False, "error": f"Case {cid} not found."}

                new_status = "in_review" if before["status"] == "open" else before["status"]
                cur.execute(
                    """
                    UPDATE cases
                    SET assigned_to = %s, status = %s::case_status
                    WHERE case_id = %s
                    """,
                    (rid, new_status, cid),
                )
                cur.execute(
                    "SELECT case_id, status, assigned_to FROM cases WHERE case_id = %s",
                    (cid,),
                )
                after = cur.fetchone()

        return json_safe({
            "ok": True,
            "case_id": cid,
            "before": {"assigned_to": before["assigned_to"], "status": before["status"]},
            "after": {"assigned_to": after["assigned_to"], "assignee": rname, "status": after["status"]},
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@audited(is_write=True)
def update_case_status(case_id: int, status: str, reason: str) -> dict:
    """
    Update a case's status and add the reason as an agent note in the same
    transaction. The status must be a valid case_status value. The reason is
    required and non-empty. Errors if the case already has that status.
    """
    try:
        cid = int(case_id)
        if status not in _VALID_STATUS:
            return {"ok": False, "error": f"Invalid status '{status}'. Valid: {sorted(_VALID_STATUS)}."}
        if not reason or not str(reason).strip():
            return {"ok": False, "error": "reason is required and must be non-empty."}

        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT case_id, status FROM cases WHERE case_id = %s",
                    (cid,),
                )
                before = cur.fetchone()
                if not before:
                    return {"ok": False, "error": f"Case {cid} not found."}
                if before["status"] == status:
                    return {"ok": False, "error": f"Case {cid} is already '{status}'."}

                cur.execute(
                    "UPDATE cases SET status = %s::case_status WHERE case_id = %s",
                    (status, cid),
                )
                cur.execute(
                    """
                    INSERT INTO case_notes (case_id, author_type, body)
                    VALUES (%s, 'agent', %s)
                    """,
                    (cid, str(reason).strip()),
                )
                cur.execute(
                    "SELECT case_id, status, resolved_at FROM cases WHERE case_id = %s",
                    (cid,),
                )
                after = cur.fetchone()

        return json_safe({
            "ok": True,
            "case_id": cid,
            "before": {"status": before["status"]},
            "after": {"status": after["status"], "resolved_at": after["resolved_at"]},
            "note": str(reason).strip(),
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@audited(is_write=True)
def add_case_note(case_id: int, body: str) -> dict:
    """
    Add a note to a case (author_type = 'agent'). The body must be non-empty.
    Returns the note id and timestamp.
    """
    try:
        cid = int(case_id)
        if not body or not str(body).strip():
            return {"ok": False, "error": "body is required and must be non-empty."}

        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT case_id FROM cases WHERE case_id = %s", (cid,))
                if not cur.fetchone():
                    return {"ok": False, "error": f"Case {cid} not found."}

                cur.execute(
                    """
                    INSERT INTO case_notes (case_id, author_type, body)
                    VALUES (%s, 'agent', %s)
                    RETURNING note_id, created_at
                    """,
                    (cid, str(body).strip()),
                )
                row = cur.fetchone()

        return json_safe({
            "ok": True,
            "case_id": cid,
            "note_id": row["note_id"],
            "created_at": row["created_at"],
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@audited(is_write=True)
def create_watchlist(reviewer: str, name: str, pages: list) -> dict:
    """
    Create a watchlist for a reviewer with a set of pages. Each page is a dict
    with 'wiki' and 'page_title'. Returns the watchlist id and page count.
    """
    try:
        if not reviewer or not str(reviewer).strip():
            return {"ok": False, "error": "reviewer is required."}
        if not name or not str(name).strip():
            return {"ok": False, "error": "name is required."}
        if not pages or not isinstance(pages, list):
            return {"ok": False, "error": "pages must be a non-empty list of {wiki, page_title} dicts."}

        for p in pages:
            if not isinstance(p, dict) or "wiki" not in p or "page_title" not in p:
                return {"ok": False, "error": "Each page must have 'wiki' and 'page_title'."}

        reviewer = str(reviewer).strip()
        name = str(name).strip()

        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if reviewer.isdigit():
                    cur.execute(
                        "SELECT reviewer_id FROM reviewers WHERE reviewer_id = %s",
                        (int(reviewer),),
                    )
                else:
                    cur.execute(
                        "SELECT reviewer_id FROM reviewers WHERE LOWER(display_name) = LOWER(%s)",
                        (reviewer,),
                    )
                matches = cur.fetchall()
                if not matches:
                    return {"ok": False, "error": f"Reviewer '{reviewer}' not found."}
                if len(matches) > 1:
                    return {"ok": False, "error": f"Reviewer '{reviewer}' is ambiguous. Use the reviewer ID."}

                rid = matches[0]["reviewer_id"]

                cur.execute(
                    """
                    INSERT INTO watchlists (reviewer_id, name)
                    VALUES (%s, %s)
                    RETURNING watchlist_id
                    """,
                    (rid, name),
                )
                wl_id = cur.fetchone()["watchlist_id"]

                for p in pages:
                    cur.execute(
                        """
                        INSERT INTO watchlist_pages (watchlist_id, wiki, page_title)
                        VALUES (%s, %s, %s)
                        """,
                        (wl_id, p["wiki"], p["page_title"]),
                    )

        return json_safe({
            "ok": True,
            "watchlist_id": wl_id,
            "reviewer": reviewer,
            "name": name,
            "pages_added": len(pages),
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@audited(is_write=True)
def bulk_dismiss(case_ids: list, reason: str, confirm: bool = False) -> dict:
    """
    Dismiss up to 50 cases at once. Must be called with confirm=False first to
    get a preview of which cases would change. Only call with confirm=True
    after the user has agreed. Adds the reason as an agent note on each case.
    Skips cases already dismissed and reports them separately.
    """
    try:
        if not case_ids or not isinstance(case_ids, list):
            return {"ok": False, "error": "case_ids must be a non-empty list."}
        if len(case_ids) > 50:
            return {"ok": False, "error": f"At most 50 cases per call (got {len(case_ids)})."}
        if not reason or not str(reason).strip():
            return {"ok": False, "error": "reason is required and must be non-empty."}

        ids = [int(cid) for cid in case_ids]
        reason = str(reason).strip()

        placeholders = ", ".join(["%s"] * len(ids))

        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                    SELECT case_id, status, wiki, page_title
                    FROM cases WHERE case_id IN ({placeholders})
                    """,
                    ids,
                )
                rows = cur.fetchall()

                if not rows:
                    return {"ok": False, "error": f"No cases found for IDs {ids}."}

                found = {r["case_id"]: r for r in rows}
                not_found = [cid for cid in ids if cid not in found]
                to_dismiss = [r for r in rows if r["status"] != "dismissed"]
                already_dismissed = [r for r in rows if r["status"] == "dismissed"]

                if not confirm:
                    return json_safe({
                        "ok": True,
                        "confirm": False,
                        "would_dismiss": [
                            {"case_id": r["case_id"], "wiki": r["wiki"],
                             "page_title": r["page_title"], "current_status": r["status"]}
                            for r in to_dismiss
                        ],
                        "already_dismissed": [r["case_id"] for r in already_dismissed],
                        "not_found": not_found,
                        "count_to_dismiss": len(to_dismiss),
                        "message": f"{len(to_dismiss)} case(s) would be dismissed. Call with confirm=true to proceed.",
                    })

                for r in to_dismiss:
                    cur.execute(
                        "UPDATE cases SET status = 'dismissed'::case_status WHERE case_id = %s",
                        (r["case_id"],),
                    )
                    cur.execute(
                        """
                        INSERT INTO case_notes (case_id, author_type, body)
                        VALUES (%s, 'agent', %s)
                        """,
                        (r["case_id"], reason),
                    )

        return json_safe({
            "ok": True,
            "confirm": True,
            "dismissed": [r["case_id"] for r in to_dismiss],
            "skipped_already_dismissed": [r["case_id"] for r in already_dismissed],
            "not_found": not_found,
            "count_dismissed": len(to_dismiss),
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
