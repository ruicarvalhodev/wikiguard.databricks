"""
WikiGuard silver transform.

Parses the bronze ``raw_recentchange`` table into a typed ``silver.edits``
table, filters canary events and rows that failed JSON parsing, derives
enrichment columns, and deduplicates via a MERGE on ``event_id`` so connector
restarts don't create duplicate rows.

``spark`` is always passed in from the caller; this module never creates a
SparkSession.
"""
from __future__ import annotations

import logging

from delta.tables import DeltaTable
from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col,
    current_timestamp,
    from_json,
    split,
)

from wikiguard.common.schemas import RECENTCHANGE_SCHEMA
from wikiguard.config import CONFIG, Config

log = logging.getLogger(__name__)


def process_once(spark: SparkSession, config: Config = CONFIG) -> int:
    """
    Run one silver transform batch with ``Trigger.AvailableNow``.

    Reads new rows from the bronze table, parses and enriches them, then
    merges into the silver edits table so replayed connector events do not
    create duplicates.  The silver table is created on the first run.
    Waits for the query to complete before returning.

    Parameters
    ----------
    spark:
        Active SparkSession.
    config:
        Runtime configuration.

    Returns
    -------
    int
        Number of bronze rows read in this run.
    """

    def _merge_batch(batch_df, batch_id):
        if batch_df.isEmpty():
            return

        deduped = batch_df.dropDuplicates(["event_id"])

        if not spark.catalog.tableExists(config.silver_edits_table):
            (
                deduped.write
                .format("delta")
                .partitionBy("event_date")
                .saveAsTable(config.silver_edits_table)
            )
            return

        (
            DeltaTable.forName(spark, config.silver_edits_table)
            .alias("t")
            .merge(
                deduped.alias("s"),
                # Restrict the target scan to recent partitions so the MERGE
                # never does a full table scan.  Connector replays arrive
                # within seconds of a restart, so 1 day is more than enough.
                "t.event_id = s.event_id "
                "AND t.event_date >= date_sub(current_date(), 1)",
            )
            .whenNotMatchedInsertAll()
            .execute()
        )

    silver_df = (
        spark.readStream
        .table(config.bronze_table)
        .select(
            from_json(col("payload"), RECENTCHANGE_SCHEMA).alias("e"),
            col("ingest_ts"),
        )
        # Drop canary events and rows where JSON parsing failed
        .filter(
            (col("e.meta.domain") != "canary") & col("e.meta.id").isNotNull()
        )
        .select(
            col("e.meta.id").alias("event_id"),
            col("e.meta.dt").cast("timestamp").alias("event_ts"),
            col("e.meta.dt").cast("timestamp").cast("date").alias("event_date"),
            col("e.wiki").alias("wiki"),
            col("e.server_name").alias("server_name"),
            col("e.type").alias("type"),
            col("e.namespace").alias("namespace"),
            col("e.title").alias("title"),
            col("e.user").alias("user"),
            col("e.comment").alias("comment"),
            # First label of the server_name, e.g. "pt" from "pt.wikipedia.org"
            split(col("e.server_name"), r"\.")[0].alias("lang"),
            col("e.server_name").endswith(".wikipedia.org").alias("is_wikipedia"),
            col("e.bot").alias("is_bot"),
            col("e.minor").alias("is_minor"),
            # patrolled is absent on wikis where patrolling is disabled;
            # NULL here means "not applicable", which is distinct from false.
            col("e.patrolled").cast("string").alias("patrol_state"),
            col("e.length.old").alias("length_old"),
            col("e.length.new").alias("length_new"),
            col("e.revision.old").alias("rev_old"),
            col("e.revision.new").alias("rev_new"),
            (col("e.length.new") - col("e.length.old")).alias("byte_delta"),
            col("e.user").startswith("~").alias("is_temp_account"),
            (
                (col("e.length.old") > 2000) & (col("e.length.new") < 500)
            ).alias("is_blanking"),
            col("e.comment").rlike(
                r"(?i)\b(revert|undo|undid|rvv?|desfe|revertid)\b"
            ).alias("is_revert"),
            col("ingest_ts"),
            current_timestamp().alias("silver_ts"),
        )
    )

    query = (
        silver_df.writeStream
        .foreachBatch(_merge_batch)
        .trigger(availableNow=True)
        .option("checkpointLocation", f"{config.checkpoint_path}/silver")
        .start()
    )

    query.awaitTermination()
    rows = sum(int(p.get("numInputRows") or 0) for p in query.recentProgress)
    return rows
