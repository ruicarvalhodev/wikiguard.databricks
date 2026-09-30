"""
WikiGuard silver candidates transform.

Filters ``silver.edits`` to Wikipedia article edits, assigns a risk tier,
and appends to ``silver.candidates`` for downstream scoring (task 2.3).

Tier rules (first match wins):

    N — page creation (type = 'new') — not scoreable by the Lift Wing model
    A — temporary account AND behavioural
    B — registered account AND behavioural
    C — temporary account, not behavioural
    D — everything else (registered, not behavioural)

Behavioural = byte_delta < −800 OR is_blanking (both null-coalesced to False).
Null is_temp_account is treated as False (admin-hidden usernames).
"""
from __future__ import annotations

import logging

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    coalesce,
    col,
    current_timestamp,
    lit,
    when,
)

from wikiguard.config import CONFIG, Config

log = logging.getLogger(__name__)


def process_once(spark: SparkSession, config: Config = CONFIG) -> int:
    """
    Run one candidates transform batch with ``Trigger.AvailableNow``.

    Reads new rows from ``silver.edits``, filters to Wikipedia article edits,
    assigns a risk tier, and appends to ``silver.candidates``.  The table is
    created on the first run.

    Parameters
    ----------
    spark:
        Active SparkSession.
    config:
        Runtime configuration.

    Returns
    -------
    int
        Number of silver rows read in this run.
    """
    # Null-safe: coalesce so downstream boolean algebra is never NULL.
    is_behavioural = (
        coalesce(col("byte_delta") < -800, lit(False))
        | coalesce(col("is_blanking"), lit(False))
    )

    # Treat null is_temp_account as False (admin-hidden usernames).
    is_temp = coalesce(col("is_temp_account"), lit(False))

    tier = (
        when(col("type") == "new",      lit("N"))  # page creation
        .when(is_temp  &  is_behavioural, lit("A"))
        .when(~is_temp &  is_behavioural, lit("B"))
        .when(is_temp  & ~is_behavioural, lit("C"))
        .otherwise(lit("D"))
    )

    candidates_df = (
        spark.readStream
        # silver.edits is written with MERGE; without this option the stream
        # fails when it encounters a non-append commit.
        .option("skipChangeCommits", "true")
        .table(config.silver_edits_table)
        .filter(
            col("is_wikipedia")
            & (col("namespace") == 0)
            & ~col("is_bot")
            & col("type").isin("edit", "new")
        )
        .select(
            "event_id", "event_ts", "event_date", "wiki", "lang",
            "title", "user", "comment", "type", "namespace",
            "is_wikipedia", "is_bot", "is_temp_account",
            "byte_delta", "is_blanking", "patrol_state",
            "rev_old", "rev_new",
            is_behavioural.alias("is_behavioural"),
            tier.alias("tier"),
            current_timestamp().alias("candidates_ts"),
        )
    )

    query = (
        candidates_df.writeStream
        .option("checkpointLocation", f"{config.checkpoint_path}/candidates")
        .partitionBy("event_date")
        .trigger(availableNow=True)
        .toTable(config.silver_candidates_table)
    )

    query.awaitTermination()
    rows = sum(int(p.get("numInputRows") or 0) for p in query.recentProgress)
    return rows
