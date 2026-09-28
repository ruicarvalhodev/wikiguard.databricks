"""
WikiGuard Auto Loader bronze ingestion.

Reads raw JSONL files from the UC Volume ``events/`` folder and appends them
to the bronze Delta table using Auto Loader with ``Trigger.AvailableNow``.

Serverless compute only supports ``Trigger.AvailableNow`` for Structured
Streaming — time-based triggers (``processingTime``) and the default no-trigger
mode are not available.  :func:`run_loop` handles the wait between runs in
ordinary Python so the job stays alive between batches.

``spark`` is always passed in from the notebook; this module never creates a
SparkSession.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_timestamp

from wikiguard.config import CONFIG, Config

log = logging.getLogger(__name__)


def ingest_once(spark: SparkSession, config: Config = CONFIG) -> int:
    """
    Run one Auto Loader batch with ``Trigger.AvailableNow``.

    Processes all new files in ``config.events_path`` and appends them to
    ``config.bronze_table``.  The table is created on the first run.
    Waits for the query to complete before returning.

    Parameters
    ----------
    spark:
        Active SparkSession (passed in from the notebook).
    config:
        Runtime configuration.

    Returns
    -------
    int
        Total rows written in this run (sum across all micro-batches).
    """
    query = (
        spark.readStream
        .format("cloudFiles")
        .option("cloudFiles.format", "text")
        .option("cloudFiles.schemaLocation", f"{config.schema_path}/bronze")
        .option("pathGlobFilter", "events_*.jsonl")
        .load(config.events_path)
        .select(
            col("value").alias("payload"),
            current_timestamp().alias("ingest_ts"),
            col("_metadata.file_path").alias("source_file"),
        )
        .writeStream
        .option("checkpointLocation", f"{config.checkpoint_path}/bronze")
        .trigger(availableNow=True)
        .toTable(config.bronze_table)
    )

    query.awaitTermination()
    rows = sum(int(p.get("numInputRows") or 0) for p in query.recentProgress)
    return rows


def run_loop(
    spark: SparkSession,
    config: Config = CONFIG,
    interval_seconds: float = 10.0,
    max_seconds: Optional[float] = None,
) -> None:
    """
    Repeatedly call :func:`ingest_once`, sleeping *interval_seconds* between
    runs.

    Designed to run as a continuous Databricks job.  Logs the row count and
    wall-clock duration for every run so progress is visible in the job log.

    Parameters
    ----------
    spark:
        Active SparkSession.
    config:
        Runtime configuration.
    interval_seconds:
        Seconds to sleep between ingest runs.
    max_seconds:
        Stop after this many seconds.  ``None`` runs forever.
    """
    deadline = (time.time() + max_seconds) if max_seconds else None
    run = 0

    while deadline is None or time.time() < deadline:
        run += 1
        t0 = time.time()
        rows = ingest_once(spark, config)
        elapsed = time.time() - t0
        log.info("run=%d rows=%d duration=%.1fs", run, rows, elapsed)

        if deadline and time.time() >= deadline:
            break

        time.sleep(interval_seconds)
