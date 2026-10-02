"""
Build gold analytics tables from silver.case_events and lb_agent_actions_history.

``build_analytics`` rebuilds all gold tables via CREATE OR REPLACE TABLE … AS
SELECT (idempotent full refresh) and appends one monitoring row to
``gold.pipeline_health`` so the run history is preserved.
"""
from __future__ import annotations

from pyspark.sql import SparkSession

from wikiguard.config import Config


def _count(spark: SparkSession, table: str) -> int:
    """Return the row count of a table."""
    return spark.sql(f"SELECT COUNT(*) AS n FROM {table}").first()["n"]


def build_analytics(spark: SparkSession, config: Config, rows_added: int) -> dict:
    """
    Rebuild all gold analytics tables and append one row to pipeline_health.

    Every table except ``pipeline_health`` is a full rebuild
    (CREATE OR REPLACE TABLE … AS SELECT), so the function is idempotent.
    ``pipeline_health`` is append-only so the monitoring history is never
    overwritten.

    Returns a summary dict with the row count for each table.
    """

    # ------------------------------------------------------------------ #
    # a. gold.fact_case_transitions                                        #
    #    One row per status change; dwell time in the previous status.     #
    # ------------------------------------------------------------------ #
    spark.sql(f"""
        CREATE OR REPLACE TABLE {config.gold_fact_case_transitions} AS
        WITH ordered AS (
            SELECT *,
                   LAG(status) OVER (
                       PARTITION BY case_id ORDER BY event_time, sort_by
                   ) AS prev_status,
                   ROW_NUMBER() OVER (
                       PARTITION BY case_id ORDER BY event_time, sort_by
                   ) AS rn
            FROM {config.silver_case_events_table}
        ),
        transitions AS (
            SELECT
                case_id, wiki, tier,
                prev_status   AS from_status,
                status        AS to_status,
                event_time    AS changed_at,
                assigned_to,
                is_snapshot
            FROM ordered
            -- First event of each case (rn=1) or a status change.
            WHERE rn = 1 OR status <> prev_status
        )
        SELECT
            case_id, wiki, tier,
            from_status, to_status, changed_at,
            assigned_to, is_snapshot,
            CAST(
                unix_timestamp(changed_at)
                - unix_timestamp(LAG(changed_at) OVER (
                    PARTITION BY case_id ORDER BY changed_at
                ))
            AS BIGINT) AS seconds_in_previous_state
        FROM transitions
    """)
    spark.sql(
        f"COMMENT ON TABLE {config.gold_fact_case_transitions} IS "
        "'One row per case status change derived from silver.case_events. "
        "seconds_in_previous_state is the dwell time in the preceding status.'"
    )
    transitions_count = _count(spark, config.gold_fact_case_transitions)

    # ------------------------------------------------------------------ #
    # b. gold.fact_agent_activity                                          #
    #    One row per tool call; ok parsed from JSON tool_output.          #
    # ------------------------------------------------------------------ #
    spark.sql(f"""
        CREATE OR REPLACE TABLE {config.gold_fact_agent_activity} AS
        SELECT
            action_id,
            session_id,
            tool_name,
            is_write,
            latency_ms,
            created_at,
            CAST(created_at AS DATE)                               AS event_date,
            CASE get_json_object(CAST(tool_output AS STRING), '$.ok')
                WHEN 'true'  THEN TRUE
                WHEN 'false' THEN FALSE
                ELSE NULL
            END                                                    AS ok
        FROM {config.lb_agent_actions_history}
        WHERE _pg_change_type = 'insert'
    """)
    spark.sql(
        f"COMMENT ON TABLE {config.gold_fact_agent_activity} IS "
        "'One row per agent tool call from lb_agent_actions_history insert events. "
        "ok is parsed from the JSON tool_output field; NULL means the value was absent or truncated.'"
    )
    agent_activity_count = _count(spark, config.gold_fact_agent_activity)

    # ------------------------------------------------------------------ #
    # c. gold.agg_agent_daily                                              #
    #    Daily roll-up: calls, errors, latency, sessions per tool.        #
    # ------------------------------------------------------------------ #
    spark.sql(f"""
        CREATE OR REPLACE TABLE {config.gold_agg_agent_daily} AS
        SELECT
            event_date,
            tool_name,
            COUNT(*)                                                    AS calls,
            SUM(CASE WHEN is_write THEN 1 ELSE 0 END)                  AS writes,
            SUM(CASE WHEN ok = FALSE THEN 1 ELSE 0 END)                AS errors,
            ROUND(
                SUM(CASE WHEN ok = FALSE THEN 1 ELSE 0 END) * 1.0
                / COUNT(*), 4
            )                                                           AS error_rate,
            ROUND(AVG(latency_ms), 2)                                  AS avg_latency_ms,
            PERCENTILE_APPROX(latency_ms, 0.95)                        AS p95_latency_ms,
            COUNT(DISTINCT session_id)                                  AS sessions
        FROM {config.gold_fact_agent_activity}
        GROUP BY event_date, tool_name
        ORDER BY event_date DESC, tool_name
    """)
    spark.sql(
        f"COMMENT ON TABLE {config.gold_agg_agent_daily} IS "
        "'Daily roll-up of agent tool calls from fact_agent_activity: "
        "call count, writes, errors, error_rate, average and p95 latency, and distinct sessions per tool.'"
    )
    agg_agent_count = _count(spark, config.gold_agg_agent_daily)

    # ------------------------------------------------------------------ #
    # d. gold.agg_daily_triage                                            #
    #    Daily triage activity per tier with median close time.           #
    # ------------------------------------------------------------------ #
    spark.sql(f"""
        CREATE OR REPLACE TABLE {config.gold_agg_daily_triage} AS
        WITH created AS (
            -- Count each case once, using its original created_at date.
            SELECT
                tier,
                CAST(created_at AS DATE)        AS day,
                COUNT(DISTINCT case_id)         AS cases_created
            FROM {config.silver_case_events_table}
            GROUP BY tier, CAST(created_at AS DATE)
        ),
        status_counts AS (
            -- Non-snapshot transitions into terminal/escalated statuses.
            SELECT
                tier,
                CAST(changed_at AS DATE)        AS day,
                SUM(CASE WHEN to_status = 'escalated'  THEN 1 ELSE 0 END) AS escalated,
                SUM(CASE WHEN to_status = 'resolved'   THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN to_status = 'dismissed'  THEN 1 ELSE 0 END) AS dismissed
            FROM {config.gold_fact_case_transitions}
            WHERE to_status IN ('escalated', 'resolved', 'dismissed')
              AND is_snapshot = FALSE
            GROUP BY tier, CAST(changed_at AS DATE)
        ),
        close_median AS (
            -- Median hours from case creation to resolution or dismissal.
            SELECT
                t.tier,
                CAST(t.changed_at AS DATE)                              AS day,
                PERCENTILE_APPROX(
                    (unix_timestamp(t.changed_at)
                     - unix_timestamp(ce.created_at)) / 3600.0, 0.5
                )                                                       AS median_hours_to_close
            FROM {config.gold_fact_case_transitions} t
            JOIN (
                SELECT DISTINCT case_id, created_at
                FROM {config.silver_case_events_table}
            ) ce ON t.case_id = ce.case_id
            WHERE t.to_status IN ('resolved', 'dismissed')
              AND t.is_snapshot = FALSE
            GROUP BY t.tier, CAST(t.changed_at AS DATE)
        ),
        all_days AS (
            SELECT tier, day FROM created
            UNION
            SELECT tier, day FROM status_counts
        )
        SELECT
            a.day,
            a.tier,
            COALESCE(c.cases_created,  0)   AS cases_created,
            COALESCE(sc.escalated,     0)   AS escalated,
            COALESCE(sc.resolved,      0)   AS resolved,
            COALESCE(sc.dismissed,     0)   AS dismissed,
            cm.median_hours_to_close
        FROM all_days a
        LEFT JOIN created       c  ON c.tier  = a.tier AND c.day  = a.day
        LEFT JOIN status_counts sc ON sc.tier = a.tier AND sc.day = a.day
        LEFT JOIN close_median  cm ON cm.tier = a.tier AND cm.day = a.day
        ORDER BY a.day DESC, a.tier
    """)
    spark.sql(
        f"COMMENT ON TABLE {config.gold_agg_daily_triage} IS "
        "'Daily triage activity per tier: cases created (by created_at), transitions into "
        "escalated/resolved/dismissed (non-snapshot only), and median hours to close.'"
    )
    agg_triage_count = _count(spark, config.gold_agg_daily_triage)

    # ------------------------------------------------------------------ #
    # e. gold.pipeline_health (append-only monitoring table)               #
    # ------------------------------------------------------------------ #
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {config.gold_pipeline_health} (
            run_at              TIMESTAMP_NTZ NOT NULL,
            case_events_added   BIGINT,
            transitions_total   BIGINT,
            agent_actions_total BIGINT,
            latest_cdf_ts       TIMESTAMP_NTZ,
            cdf_lag_seconds     BIGINT
        )
    """)
    spark.sql(f"""
        INSERT INTO {config.gold_pipeline_health}
        WITH cdf AS (
            SELECT MAX(_timestamp) AS latest_cdf_ts FROM {config.lb_cases_history}
        )
        SELECT
            current_timestamp()                                         AS run_at,
            BIGINT({int(rows_added)})                                   AS case_events_added,
            (SELECT COUNT(*) FROM {config.gold_fact_case_transitions})  AS transitions_total,
            (SELECT COUNT(*) FROM {config.gold_fact_agent_activity})    AS agent_actions_total,
            cdf.latest_cdf_ts,
            CAST(
                unix_timestamp(current_timestamp())
                - unix_timestamp(cdf.latest_cdf_ts)
            AS BIGINT)                                                  AS cdf_lag_seconds
        FROM cdf
    """)
    spark.sql(
        f"COMMENT ON TABLE {config.gold_pipeline_health} IS "
        "'Monitoring table: one row appended per analytics pipeline run with row counts "
        "and cdf_lag_seconds showing how fresh the Lakebase change feed is.'"
    )

    return {
        "case_events_added": rows_added,
        "transitions": transitions_count,
        "agent_activity": agent_activity_count,
        "agg_agent_daily": agg_agent_count,
        "agg_daily_triage": agg_triage_count,
    }
