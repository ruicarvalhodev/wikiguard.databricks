"""
Sync gold triage candidates into Lakebase cases.

Only inserts new rows — existing cases are never overwritten.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from wikiguard.lakebase.client import connect

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

    from wikiguard.config import Config

# Columns from gold.triage_candidates to copy into cases.
_INSERT_COLUMNS = [
    "wiki",
    "rev_id",
    "rev_old",
    "lang",
    "page_title",
    "editor",
    "is_temp_account",
    "byte_delta",
    "patrol_state",
    "revert_risk",
    "model_version",
    "tier",
    "priority",
    "diff_url",
    "event_ts",
]

_INSERT_SQL = """
    INSERT INTO cases (
        wiki, rev_id, rev_old, lang, page_title, editor,
        is_temp_account, byte_delta, patrol_state, revert_risk,
        model_version, tier, priority, diff_url, event_ts
    )
    VALUES (
        %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s, %s
    )
    ON CONFLICT (wiki, rev_id) DO NOTHING
"""


def sync_cases(spark: SparkSession, config: Config, lookback_hours: int = 24) -> dict:
    """
    Copy new queue entries from gold.triage_candidates into Lakebase cases.

    Reads only rows with ``scored_at`` in the last *lookback_hours* to keep
    each run small — older rows were already synced by earlier runs.

    Inserts with ``ON CONFLICT (wiki, rev_id) DO NOTHING`` so existing cases
    are never overwritten — their status, assignment and notes belong to the
    reviewers and the agent.

    Returns
    -------
    dict
        ``{"candidates": n, "inserted": n, "already_present": n}``
    """
    # --- Read recent gold candidates ---
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    gold_df = (
        spark.table(config.gold_triage_table)
        .where(f"scored_at >= '{cutoff_str}'")
        .select(*_INSERT_COLUMNS)
    )

    rows = gold_df.collect()
    candidates = len(rows)

    # --- Insert into Lakebase ---
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM cases")
            (before,) = cur.fetchone()

            if candidates > 0:
                batch = [tuple(row) for row in rows]
                cur.executemany(_INSERT_SQL, batch)

            cur.execute("SELECT count(*) FROM cases")
            (after,) = cur.fetchone()

    inserted = after - before
    already_present = candidates - inserted

    return {
        "candidates": candidates,
        "inserted": inserted,
        "already_present": already_present,
    }
