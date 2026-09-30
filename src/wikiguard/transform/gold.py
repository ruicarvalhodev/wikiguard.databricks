"""
WikiGuard gold transform.

Builds the triage_candidates table: the prioritised review queue that
Phase 3 copies into Lakebase as cases.  The table is rebuilt on every
scoring run so it is always in step with the latest Lift Wing scores.

Priority bands (higher = review first):

    Tier A: 200 + round(revert_risk * 100)   → 200–300
    Tier B: 100 + round(revert_risk * 100)   → 100–200

Every tier-A row sorts before every tier-B row regardless of score,
and within each tier the riskiest edits come first.
"""
from __future__ import annotations

import logging

from pyspark.sql import SparkSession

from wikiguard.config import CONFIG, Config

log = logging.getLogger(__name__)


def build_triage_candidates(spark: SparkSession, config: Config = CONFIG) -> int:
    """
    Rebuild ``gold.triage_candidates`` with a single CREATE OR REPLACE TABLE.

    Joins ``silver.candidates`` against ``silver.edit_risk``, keeps only
    tier A and B rows with a non-null score, deduplicates on ``(wiki, rev_id)``
    by taking the latest ``scored_at``, and assigns a priority integer.

    Parameters
    ----------
    spark:
        Active SparkSession.
    config:
        Runtime configuration.

    Returns
    -------
    int
        Row count of the rebuilt table.
    """
    spark.sql(f"""
        CREATE OR REPLACE TABLE {config.gold_triage_table}
        USING DELTA
        AS
        WITH deduped_risk AS (
            -- Deduplicate edit_risk in case a revision was scored more than once;
            -- keep the most recent score.
            SELECT
                wiki,
                rev_id,
                revert_risk,
                model_version,
                scored_at,
                ROW_NUMBER() OVER (
                    PARTITION BY wiki, rev_id
                    ORDER BY scored_at DESC
                ) AS rn
            FROM {config.silver_edit_risk_table}
            WHERE revert_risk IS NOT NULL
        ),
        latest_risk AS (
            SELECT wiki, rev_id, revert_risk, model_version, scored_at
            FROM deduped_risk
            WHERE rn = 1
        )
        SELECT
            c.wiki,
            c.rev_new                                       AS rev_id,
            c.rev_old,
            c.lang,
            c.title                                         AS page_title,
            c.user                                          AS editor,
            COALESCE(c.is_temp_account, false)              AS is_temp_account,
            c.byte_delta,
            c.is_blanking,
            c.patrol_state,
            c.comment,
            r.revert_risk,
            r.model_version,
            c.tier,
            CAST(
                CASE c.tier
                    WHEN 'A' THEN 200 + ROUND(r.revert_risk * 100)
                    WHEN 'B' THEN 100 + ROUND(r.revert_risk * 100)
                END
            AS SMALLINT)                                    AS priority,
            CONCAT(
                'https://', c.lang,
                '.wikipedia.org/w/index.php?diff=', c.rev_new,
                '&oldid=', c.rev_old
            )                                               AS diff_url,
            c.event_ts,
            r.scored_at,
            CURRENT_TIMESTAMP()                             AS gold_ts
        FROM {config.silver_candidates_table} c
        INNER JOIN latest_risk r
            ON  c.wiki    = r.wiki
            AND c.rev_new = r.rev_id
        WHERE c.tier IN ('A', 'B')
    """)

    n = spark.table(config.gold_triage_table).count()
    log.info("gold.triage_candidates rebuilt: %d rows", n)
    return n
