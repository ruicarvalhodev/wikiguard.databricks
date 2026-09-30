"""
WikiGuard edit risk scoring.

Selects pending tier A/B candidates from silver.candidates, scores them
with the Lift Wing revertrisk model one by one, and appends results to
silver.edit_risk.  Rows that raise an exception are skipped and retried
on the next run; 422 responses are written as final (no parent revision).
"""
from __future__ import annotations

import logging

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_date, current_timestamp, date_sub
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
)

from wikiguard.common.http import session_with_retries
from wikiguard.config import CONFIG, Config
from wikiguard.enrich.liftwing import score_revision

log = logging.getLogger(__name__)

# Explicit schema avoids type-inference surprises when revert_risk is NULL
# for a whole batch (which would make Spark infer NullType).
_RESULT_SCHEMA = StructType([
    StructField("wiki",          StringType()),
    StructField("rev_id",        LongType()),
    StructField("lang",          StringType()),
    StructField("tier",          StringType()),
    StructField("revert_risk",   DoubleType()),
    StructField("prediction",    BooleanType()),
    StructField("model_version", StringType()),
    StructField("http_status",   IntegerType()),
])


def score_pending(
    spark: SparkSession,
    config: Config = CONFIG,
    max_rows: int = 300,
) -> dict:
    """
    Score pending tier A/B candidates with the Lift Wing revertrisk model.

    Selects rows from silver.candidates that are not already in
    silver.edit_risk (anti-joined on wiki + rev_id), calls the API, and
    appends results.  The edit_risk table is created on the first write.

    Parameters
    ----------
    spark:
        Active SparkSession.
    config:
        Runtime configuration.
    max_rows:
        Maximum number of revisions to score in one run.

    Returns
    -------
    dict
        Summary with keys: selected, scored, no_parent_422, failed.
    """
    candidates = spark.table(config.silver_candidates_table)

    base = candidates.filter(
        col("tier").isin("A", "B")
        & (col("event_date") >= date_sub(current_date(), 1))
        & col("rev_new").isNotNull()
    )

    # Anti-join against already-scored revisions (wiki + rev_id are composite key).
    if spark.catalog.tableExists(config.silver_edit_risk_table):
        already = (
            spark.table(config.silver_edit_risk_table)
            .select(col("wiki"), col("rev_id").alias("rev_new"))
        )
        pending = base.join(already, on=["wiki", "rev_new"], how="left_anti")
    else:
        pending = base

    rows = (
        pending
        .orderBy(col("event_ts").desc())
        .limit(max_rows)
        .select("wiki", "rev_new", "lang", "tier")
        .collect()
    )

    n_selected  = len(rows)
    n_scored    = 0
    n_no_parent = 0
    n_failed    = 0
    results     = []

    session = session_with_retries(config.contact_email)

    for row in rows:
        rev_id = int(row.rev_new)
        try:
            result = score_revision(session, config.liftwing_url, rev_id, row.lang)
            results.append({
                "wiki":          row.wiki,
                "rev_id":        rev_id,
                "lang":          row.lang,
                "tier":          row.tier,
                "revert_risk":   result["revert_risk"],
                "prediction":    result["prediction"],
                "model_version": result["model_version"],
                "http_status":   result["http_status"],
            })
            if result["http_status"] == 422:
                n_no_parent += 1
            else:
                n_scored += 1
        except Exception as exc:
            n_failed += 1
            log.warning("rev_id=%d wiki=%s failed: %s", rev_id, row.wiki, exc)

    if results:
        (
            spark.createDataFrame(results, schema=_RESULT_SCHEMA)
            .withColumn("scored_at", current_timestamp())
            .write.format("delta").mode("append")
            .saveAsTable(config.silver_edit_risk_table)
        )

    return {
        "selected":      n_selected,
        "scored":        n_scored,
        "no_parent_422": n_no_parent,
        "failed":        n_failed,
    }
