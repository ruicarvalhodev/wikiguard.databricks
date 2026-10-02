"""
Incremental CDF → silver.case_events using Structured Streaming.

``process_once`` reads new rows from ``lb_cases_history``, drops pre-images,
and appends clean state events to ``silver.case_events``.
"""
from __future__ import annotations

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from wikiguard.config import Config


def process_once(spark: SparkSession, config: Config) -> int:
    """
    Read new CDF rows from lb_cases_history, keep only the rows that describe
    the state *after* a change (insert, update_postimage, delete), and append
    them to silver.case_events.

    Uses ``trigger(availableNow=True)`` so each call runs as a bounded
    micro-batch on serverless compute and returns when all currently available
    data has been processed.  The checkpoint guarantees exactly-once delivery
    — re-running never duplicates rows.

    Returns the number of rows appended this run.
    """
    checkpoint_dir = f"{config.checkpoint_path}/case_events"

    stream_df = (
        spark.readStream
        .table(config.lb_cases_history)
        # Drop pre-images; we only want the state after each change.
        .where(F.col("_pg_change_type").isin("insert", "update_postimage", "delete"))
        .select(
            F.col("case_id"),
            F.col("wiki"),
            F.col("lang"),
            F.col("tier"),
            F.col("priority"),
            F.col("revert_risk"),
            F.col("assigned_to"),
            F.col("created_at"),
            # Delete rows carry no status in the source; use the sentinel 'deleted'.
            F.when(F.col("_pg_change_type") == "delete", F.lit("deleted"))
             .otherwise(F.col("status"))
             .alias("status"),
            # For deletes, use the CDF capture time as the event time because
            # updated_at is absent from the source row after deletion.
            F.when(F.col("_pg_change_type") == "delete", F.col("_timestamp"))
             .otherwise(F.col("updated_at"))
             .alias("event_time"),
            # Rows from the initial snapshot have _pg_xid = 0.
            (F.col("_pg_xid") == F.lit(0)).alias("is_snapshot"),
            F.col("_pg_change_type").alias("change_type"),
            F.col("_pg_lsn").alias("lsn"),
            F.col("_sort_by").alias("sort_by"),
            F.col("_timestamp").alias("cdf_ts"),
        )
    )

    target = config.silver_case_events_table
    before = (
        spark.table(target).count()
        if spark.catalog.tableExists(target)
        else 0
    )

    query = (
        stream_df
        .writeStream
        .format("delta")
        .outputMode("append")
        .option("checkpointLocation", checkpoint_dir)
        .trigger(availableNow=True)
        .toTable(target)
    )
    query.awaitTermination()

    after = spark.table(target).count()
    return after - before
