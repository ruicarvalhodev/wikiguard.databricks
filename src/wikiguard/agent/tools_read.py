"""
Agent read tools — six tools that query Lakebase, Delta, and the MediaWiki API.

Every tool returns a JSON-serialisable dict and never raises to the caller;
errors come back as {"ok": False, "error": "<message>"}.
"""
from __future__ import annotations

import logging
from typing import Optional

from psycopg.rows import dict_row

from wikiguard.agent.audit import audited, json_safe
from wikiguard.agent.sql import run_sql
from wikiguard.common.http import session_with_retries
from wikiguard.config import CONFIG
from wikiguard.enrich.similarity import warehouse_similarity_sql
from wikiguard.lakebase.client import connect

log = logging.getLogger(__name__)

_VALID_STATUS = {"open", "in_review", "escalated", "resolved", "dismissed"}


@audited(is_write=False)
def search_cases(
    status: Optional[str] = None,
    wiki: Optional[str] = None,
    tier: Optional[str] = None,
    min_risk: Optional[float] = None,
    max_risk: Optional[float] = None,
    editor: Optional[str] = None,
    assigned_to: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """
    Search the review queue with optional filters. Returns cases in queue order
    (priority DESC, revert_risk DESC, event_ts DESC). Use this to find cases
    that need attention. All filters are optional — call with no arguments to
    see the top of the queue.
    """
    try:
        limit = max(1, min(int(limit), 50))
        if status and status not in _VALID_STATUS:
            return {"ok": False, "error": f"Invalid status '{status}'. Valid: {sorted(_VALID_STATUS)}."}
        if tier and tier not in ("A", "B", "C", "D", "N"):
            return {"ok": False, "error": f"Invalid tier '{tier}'. Valid: A, B, C, D, N."}
        if max_risk is not None and not (0 <= float(max_risk) <= 1):
            return {"ok": False, "error": "max_risk must be between 0 and 1."}

        where, params = [], []
        if status:
            where.append("c.status = %s::case_status")
            params.append(status)
        if wiki:
            where.append("c.wiki = %s")
            params.append(wiki)
        if tier:
            where.append("c.tier = %s")
            params.append(tier)
        if min_risk is not None:
            where.append("c.revert_risk >= %s")
            params.append(float(min_risk))
        if max_risk is not None:
            where.append("c.revert_risk <= %s")
            params.append(float(max_risk))
        if editor:
            where.append("c.editor = %s")
            params.append(editor)
        if assigned_to:
            if str(assigned_to).isdigit():
                where.append("c.assigned_to = %s")
                params.append(int(assigned_to))
            else:
                where.append(
                    "c.assigned_to = (SELECT reviewer_id FROM reviewers "
                    "WHERE LOWER(display_name) = LOWER(%s) LIMIT 1)"
                )
                params.append(assigned_to)

        where_clause = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"""
            SELECT c.case_id, c.priority, c.tier, c.revert_risk,
                   c.wiki, c.page_title, c.editor, c.byte_delta,
                   c.status, c.assigned_to, c.diff_url,
                   r.display_name AS assignee
            FROM cases c
            LEFT JOIN reviewers r ON r.reviewer_id = c.assigned_to
            {where_clause}
            ORDER BY c.priority DESC, c.revert_risk DESC, c.event_ts DESC
            LIMIT {limit}
        """
        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return json_safe({"ok": True, "count": len(rows), "cases": rows})
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@audited(is_write=False)
def get_case_detail(case_id: int) -> dict:
    """
    Get full details for a single case: the case itself, its notes (newest first),
    and the first ~1,500 characters of the added and removed diff text from Delta.
    Combines Lakebase and Delta in one answer.
    """
    try:
        cid = int(case_id)
        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT c.case_id, c.wiki, c.rev_id, c.rev_old, c.lang,
                           c.page_title, c.editor, c.is_temp_account,
                           c.byte_delta, c.patrol_state, c.revert_risk,
                           c.model_version, c.tier, c.priority, c.diff_url,
                           c.event_ts, c.status, c.assigned_to,
                           r.display_name AS assignee,
                           c.created_at, c.updated_at, c.resolved_at
                    FROM cases c
                    LEFT JOIN reviewers r ON r.reviewer_id = c.assigned_to
                    WHERE c.case_id = %s
                    """,
                    (cid,),
                )
                case = cur.fetchone()
                if not case:
                    return {"ok": False, "error": f"Case {cid} not found."}

                cur.execute(
                    """
                    SELECT n.note_id, n.author_type, n.body, n.created_at,
                           r.display_name AS author_name
                    FROM case_notes n
                    LEFT JOIN reviewers r ON r.reviewer_id = n.author_id
                    WHERE n.case_id = %s
                    ORDER BY n.created_at DESC
                    """,
                    (cid,),
                )
                notes = cur.fetchall()

        # Diff text from Delta (via warehouse)
        try:
            diff_rows = run_sql(
                f"""
                SELECT LEFT(added_text, 1500) AS added_text,
                       LEFT(removed_text, 1500) AS removed_text
                FROM {CONFIG.silver_edit_diffs_table}
                WHERE wiki = :wiki AND rev_id = CAST(:rev_id AS BIGINT)
                LIMIT 1
                """,
                {"wiki": case["wiki"], "rev_id": int(case["rev_id"])},
            )
            if diff_rows:
                diff_text = {"available": True, **diff_rows[0]}
            else:
                diff_text = {
                    "available": False,
                    "note": "Diff text not fetched yet; see diff_url.",
                }
        except Exception as exc:
            diff_text = {"error": f"Could not fetch diff text: {exc}"}

        return json_safe({
            "ok": True,
            "case": case,
            "notes": notes,
            "diff": diff_text,
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@audited(is_write=False)
def get_editor_history(editor: str, limit: int = 20) -> dict:
    """
    Get an editor's recent edits across all wikis (from Delta silver.edits),
    plus how many of their edits are currently open cases in Lakebase.
    """
    try:
        if not editor or not editor.strip():
            return {"ok": False, "error": "editor is required."}
        limit = max(1, min(int(limit), 50))

        edits = run_sql(
            f"""
            SELECT wiki, title AS page_title, `user` AS editor,
                   byte_delta, comment, event_ts, rev_new AS rev_id
            FROM {CONFIG.silver_edits_table}
            WHERE `user` = :editor
            ORDER BY event_ts DESC
            LIMIT {limit}
            """,
            {"editor": editor},
        )

        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT count(*) AS open_cases
                    FROM cases
                    WHERE editor = %s AND status IN ('open', 'in_review')
                    """,
                    (editor,),
                )
                row = cur.fetchone()

        return json_safe({
            "ok": True,
            "editor": editor,
            "recent_edits": edits,
            "open_cases": row["open_cases"] if row else 0,
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@audited(is_write=False)
def get_page_context(wiki: str, page_title: str) -> dict:
    """
    Get the protection level and the last 5 revisions of a Wikipedia page
    from the MediaWiki API. Derives the language from the wiki identifier
    (enwiki → en). Returns a clear error if the API is slow or unreachable.
    """
    try:
        if not wiki or not page_title:
            return {"ok": False, "error": "wiki and page_title are required."}
        lang = wiki[:-4] if wiki.endswith("wiki") else wiki
        url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "prop": "info|revisions",
            "inprop": "protection",
            "titles": page_title,
            "rvprop": "user|timestamp|size|comment",
            "rvlimit": "5",
            "format": "json",
        }
        import requests  # noqa: PLC0415

        session = session_with_retries(CONFIG.contact_email)
        try:
            resp = session.get(url, params=params, timeout=10)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            return {"ok": False, "error": f"MediaWiki API timed out for {wiki}:{page_title}."}
        except requests.exceptions.RequestException as exc:
            return {"ok": False, "error": f"MediaWiki API request failed: {exc}"}

        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        if not pages:
            return {"ok": False, "error": "No pages returned from MediaWiki API."}

        page = next(iter(pages.values()))
        if "missing" in page:
            return {"ok": False, "error": f"Page '{page_title}' does not exist on {wiki}."}

        protection = [
            {"type": p.get("type"), "level": p.get("level")}
            for p in page.get("protection", [])
        ]
        revisions = [
            {"user": r.get("user"), "timestamp": r.get("timestamp"),
             "size": r.get("size"), "comment": r.get("comment")}
            for r in page.get("revisions", [])
        ]

        return json_safe({
            "ok": True,
            "wiki": wiki,
            "page_title": page_title,
            "protection": protection,
            "last_revisions": revisions,
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@audited(is_write=False)
def query_triage_metrics() -> dict:
    """
    Get queue-wide metrics: counts by status and by tier, open cases per
    reviewer, and the age of the oldest open case. Use this to understand
    the overall state of the review queue.
    """
    try:
        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT status, count(*) AS n FROM cases GROUP BY status ORDER BY status"
                )
                by_status = cur.fetchall()

                cur.execute(
                    "SELECT tier, count(*) AS n FROM cases GROUP BY tier ORDER BY tier"
                )
                by_tier = cur.fetchall()

                cur.execute(
                    """
                    SELECT r.display_name AS reviewer,
                           count(c.case_id) AS open_cases
                    FROM reviewers r
                    LEFT JOIN cases c ON c.assigned_to = r.reviewer_id
                        AND c.status IN ('open', 'in_review')
                    GROUP BY r.reviewer_id, r.display_name
                    ORDER BY open_cases DESC, r.display_name
                    """
                )
                per_reviewer = cur.fetchall()

                cur.execute(
                    """
                    SELECT count(*) AS open_cases
                    FROM cases
                    WHERE assigned_to IS NULL AND status IN ('open', 'in_review')
                    """
                )
                unassigned_row = cur.fetchone()
                unassigned_open = unassigned_row["open_cases"] if unassigned_row else 0

                cur.execute(
                    """
                    SELECT case_id,
                           EXTRACT(EPOCH FROM now() - created_at)::int AS age_seconds
                    FROM cases
                    WHERE status IN ('open', 'in_review')
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                )
                oldest = cur.fetchone()

        return json_safe({
            "ok": True,
            "by_status": by_status,
            "by_tier": by_tier,
            "open_per_reviewer": per_reviewer,
            "unassigned_open": unassigned_open,
            "oldest_open_case": oldest,
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@audited(is_write=False)
def find_similar_cases(case_id: int, k: int = 5) -> dict:
    """
    Find the k most similar edits to the given case by cosine similarity of
    diff embeddings stored in Delta. Returns similar edits with similarity
    score, page, editor, and the Lakebase case_id if the similar edit is in
    the queue.
    """
    try:
        k = max(1, min(int(k), 20))
        cid = int(case_id)

        with connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT wiki, rev_id FROM cases WHERE case_id = %s",
                    (cid,),
                )
                case = cur.fetchone()

        if not case:
            return {"ok": False, "error": f"Case {cid} not found."}

        wiki = case["wiki"]
        rev_id = int(case["rev_id"])

        similar = run_sql(
            warehouse_similarity_sql(wiki, rev_id, k),
            {"wiki": wiki, "rev_id": rev_id},
        )

        if not similar:
            return {
                "ok": True,
                "available": False,
                "similar": [],
                "note": (
                    "No embedding for this case yet, so similarity can't be computed. "
                    "This does not mean there are no similar cases."
                ),
            }

        # Check which similar edits are in Lakebase
        case_ids = {}
        if similar:
            pairs = [(r["wiki"], int(r["rev_id"])) for r in similar]
            placeholders = ", ".join(["(%s, %s)"] * len(pairs))
            flat = [v for pair in pairs for v in pair]
            with connect() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        f"SELECT case_id, wiki, rev_id FROM cases "
                        f"WHERE (wiki, rev_id) IN ({placeholders})",
                        flat,
                    )
                    for row in cur.fetchall():
                        case_ids[f"{row['wiki']}:{row['rev_id']}"] = row["case_id"]

        for r in similar:
            r["case_id"] = case_ids.get(f"{r['wiki']}:{r['rev_id']}")

        return json_safe({
            "ok": True,
            "case_id": cid,
            "wiki": wiki,
            "rev_id": rev_id,
            "count": len(similar),
            "similar": similar,
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
