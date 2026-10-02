"""
WikiGuard edit similarity.

Finds the k most similar edits in the gold triage queue based on cosine
similarity of diff embeddings stored in silver.edit_diffs.  Brute-force
Spark SQL is fast enough at a few thousand rows; no vector index is needed.
"""
from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession

from wikiguard.config import CONFIG, Config


def find_similar_cases(
    spark: SparkSession,
    config: Config = CONFIG,
    wiki: str = "",
    rev_id: int = 0,
    k: int = 5,
) -> DataFrame:
    """
    Find the k most similar edits to (wiki, rev_id) by cosine similarity.

    Computes dot-product cosine similarity against every other scored row in
    silver.edit_diffs, then joins to gold.triage_candidates for context.

    Parameters
    ----------
    spark:
        Active SparkSession.
    config:
        Runtime configuration.
    wiki:
        Wiki identifier (e.g. ``"enwiki"``).
    rev_id:
        Revision ID of the edit to compare against.
    k:
        Maximum number of similar cases to return.

    Returns
    -------
    DataFrame
        Columns: wiki, rev_id, page_title, editor, tier, revert_risk,
        similarity (DOUBLE), diff_text (first 200 chars).
    """
    rev_id = int(rev_id)  # guarantee a safe integer for f-string interpolation

    return spark.sql(f"""
        WITH target AS (
            -- The embedding we compare everything against (at most one row).
            SELECT embedding
            FROM {config.silver_edit_diffs_table}
            WHERE wiki = '{wiki}' AND rev_id = {rev_id}
              AND embedding IS NOT NULL
            LIMIT 1
        ),
        scored AS (
            SELECT
                d.wiki,
                d.rev_id,
                d.diff_text,
                -- cosine similarity = dot(a, b) / (|a| * |b|)
                aggregate(
                    zip_with(d.embedding, t.embedding,
                             (x, y) -> CAST(x AS DOUBLE) * CAST(y AS DOUBLE)),
                    0D,
                    (acc, v) -> acc + v
                )
                / NULLIF(
                    SQRT(aggregate(
                        transform(d.embedding, x -> CAST(x AS DOUBLE) * CAST(x AS DOUBLE)),
                        0D, (acc, v) -> acc + v
                    ))
                    * SQRT(aggregate(
                        transform(t.embedding, x -> CAST(x AS DOUBLE) * CAST(x AS DOUBLE)),
                        0D, (acc, v) -> acc + v
                    )),
                    0D
                ) AS similarity
            FROM {config.silver_edit_diffs_table} d
            CROSS JOIN target t
            WHERE NOT (d.wiki = '{wiki}' AND d.rev_id = {rev_id})
              AND d.embedding IS NOT NULL
        )
        SELECT
            s.wiki,
            s.rev_id,
            g.page_title,
            g.editor,
            g.tier,
            g.revert_risk,
            s.similarity,
            LEFT(s.diff_text, 200) AS diff_text
        FROM scored s
        INNER JOIN {config.gold_triage_table} g
            ON s.wiki = g.wiki AND s.rev_id = g.rev_id
        ORDER BY s.similarity DESC
        LIMIT {k}
    """)


def warehouse_similarity_sql(
    wiki: str,
    rev_id: int,
    k: int = 5,
    config: Config = CONFIG,
) -> str:
    """
    Return the similarity query as parameterised SQL for ``run_sql()``.

    Uses ``:wiki`` and ``:rev_id`` named parameters (safe for the Statement
    Execution API).  Table names are interpolated from *config* (trusted).
    *k* is cast to ``int`` before interpolation.
    """
    k = int(k)
    return f"""
        WITH target AS (
            SELECT embedding
            FROM {config.silver_edit_diffs_table}
            WHERE wiki = :wiki AND rev_id = CAST(:rev_id AS BIGINT)
              AND embedding IS NOT NULL
            LIMIT 1
        ),
        scored AS (
            SELECT
                d.wiki,
                d.rev_id,
                d.diff_text,
                aggregate(
                    zip_with(d.embedding, t.embedding,
                             (x, y) -> CAST(x AS DOUBLE) * CAST(y AS DOUBLE)),
                    0D,
                    (acc, v) -> acc + v
                )
                / NULLIF(
                    SQRT(aggregate(
                        transform(d.embedding, x -> CAST(x AS DOUBLE) * CAST(x AS DOUBLE)),
                        0D, (acc, v) -> acc + v
                    ))
                    * SQRT(aggregate(
                        transform(t.embedding, x -> CAST(x AS DOUBLE) * CAST(x AS DOUBLE)),
                        0D, (acc, v) -> acc + v
                    )),
                    0D
                ) AS similarity
            FROM {config.silver_edit_diffs_table} d
            CROSS JOIN target t
            WHERE NOT (d.wiki = :wiki AND d.rev_id = CAST(:rev_id AS BIGINT))
              AND d.embedding IS NOT NULL
        )
        SELECT
            s.wiki,
            s.rev_id,
            g.page_title,
            g.editor,
            g.tier,
            g.revert_risk,
            s.similarity,
            LEFT(s.diff_text, 200) AS diff_text
        FROM scored s
        INNER JOIN {config.gold_triage_table} g
            ON s.wiki = g.wiki AND s.rev_id = g.rev_id
        ORDER BY s.similarity DESC
        LIMIT {k}
    """
