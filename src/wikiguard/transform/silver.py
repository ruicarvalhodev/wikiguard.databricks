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
    lit,
    split,
    when,
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

        # Classify rows — first match wins
        classified = batch_df.withColumn(
            "_reason",
            when(col("e.meta.id").isNull(), lit("unparseable_json"))
            .when(col("e.meta.dt").cast("timestamp").isNull(), lit("bad_timestamp"))
            .when(col("e.wiki").isNull() | (col("e.wiki") == ""), lit("missing_wiki"))
            .otherwise(lit(None).cast("string")),
        )

        invalid = classified.filter(col("_reason").isNotNull())
        valid   = classified.filter(col("_reason").isNull())

        # --- quarantine invalid rows ---
        qcount = invalid.count()
        if qcount > 0:
            log.info("batch_id=%s quarantined=%d rows", batch_id, qcount)
            (
                invalid.select(
                    col("payload"),
                    col("_reason").alias("reason"),
                    col("ingest_ts"),
                    col("source_file"),
                    current_timestamp().alias("quarantined_ts"),
                )
                .write
                .format("delta")
                .mode("append")
                .saveAsTable(config.silver_quarantine_table)
            )

        # --- valid rows → silver.edits ---
        if valid.isEmpty():
            return

        silver_rows = valid.select(
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
            split(col("e.server_name"), r"\.")[0].alias("lang"),
            col("e.server_name").endswith(".wikipedia.org").alias("is_wikipedia"),
            col("e.bot").alias("is_bot"),
            col("e.minor").alias("is_minor"),
            # patrolled absent on wikis where patrolling is disabled;
            # NULL means "not applicable", distinct from false.
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

        deduped = silver_rows.dropDuplicates(["event_id"])

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
                "t.event_id = s.event_id "
                "AND t.event_date >= date_sub(current_date(), 1)",
            )
            .whenNotMatchedInsertAll()
            .execute()
        )

    # Stream: parse payload and keep original bronze columns so the quarantine
    # path has payload, ingest_ts, and source_file available.
    # Null-safe canary filter: rows where e.meta.domain is null (unparseable
    # JSON) pass through to the quarantine path instead of being silently
    # dropped by a plain != comparison.
    stream_df = (
        spark.readStream
        .table(config.bronze_table)
        .select(
            col("payload"),
            col("ingest_ts"),
            col("source_file"),
            from_json(col("payload"), RECENTCHANGE_SCHEMA).alias("e"),
        )
        .filter(~col("e.meta.domain").eqNullSafe("canary"))
    )

    query = (
        stream_df.writeStream
        .foreachBatch(_merge_batch)
        .trigger(availableNow=True)
        .option("checkpointLocation", f"{config.checkpoint_path}/silver")
        .start()
    )

    query.awaitTermination()
    rows = sum(int(p.get("numInputRows") or 0) for p in query.recentProgress)
    return rows
