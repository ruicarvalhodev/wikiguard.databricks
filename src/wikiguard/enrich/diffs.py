"""
WikiGuard diff enrichment.

Fetches the MediaWiki edit diff for each unenriched row in the gold triage
queue, parses added/removed text, computes text features, writes to
silver.edit_diffs, and fills embeddings via a single ai_query() UPDATE.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_timestamp, expr
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    FloatType,
    LongType,
    StringType,
    StructField,
    StructType,
)

from wikiguard.common.http import session_with_retries
from wikiguard.config import CONFIG, Config

log = logging.getLogger(__name__)

_API_URL = "https://{lang}.wikipedia.org/w/api.php"

# Explicit schema avoids NullType inference when a whole batch has null columns.
_DIFFS_SCHEMA = StructType([
    StructField("wiki",          StringType()),
    StructField("rev_id",        LongType()),
    StructField("lang",          StringType()),
    StructField("added_text",    StringType()),
    StructField("removed_text",  StringType()),
    StructField("added_chars",   LongType()),
    StructField("removed_chars", LongType()),
    StructField("upper_ratio",   DoubleType()),
    StructField("url_count",     LongType()),
    StructField("diff_text",     StringType()),
    StructField("embedding",     ArrayType(FloatType())),
    StructField("error",         StringType()),
])


def _upper_ratio(text: str | None) -> float:
    """Share of uppercase letters among all letters in *text*."""
    if not text:
        return 0.0
    letters = [c for c in text if c.isalpha()]
    return sum(1 for c in letters if c.isupper()) / len(letters) if letters else 0.0


def _url_count(text: str | None) -> int:
    """Count of http:// / https:// occurrences in *text*."""
    return len(re.findall(r"https?://", text)) if text else 0


def _diff_text(added: str | None, removed: str | None) -> str | None:
    """Embed-ready text.  Returns None only when both args are None (error row)."""
    if added is None and removed is None:
        return None
    return f"Added: {(added or '')[:1000]}\nRemoved: {(removed or '')[:1000]}"


def _extract_changed_text(tds, inline_tag: str) -> str:
    """Extract the changed portion of text from a list of diff td cells.

    For each td: if it contains ``inline_tag.diffchange`` elements, take only
    those (the actual changed spans); otherwise the whole line was added or
    removed, so take the full cell text.

    Parameters
    ----------
    tds:
        Result of ``soup.find_all("td", class_="diff-addedline")`` or the
        deleted equivalent.
    inline_tag:
        ``"ins"`` for added lines, ``"del"`` for deleted lines.
    """
    parts = []
    for td in tds:
        inlines = td.find_all(inline_tag, class_="diffchange")
        if inlines:
            parts.append(" ".join(el.get_text(strip=True) for el in inlines))
        else:
            parts.append(td.get_text(separator=" ", strip=True))
    return " ".join(parts)


def _fetch_diff(
    session,
    lang: str,
    rev_old: int,
    rev_id: int,
) -> tuple[str | None, str | None, str | None]:
    """Call the MediaWiki compare API and parse the HTML diff.

    Returns
    -------
    (added_text, removed_text, error_msg)
        *error_msg* is None on success; added/removed may be empty strings.
    """
    resp = session.get(
        _API_URL.format(lang=lang),
        params={
            "action":        "compare",
            "fromrev":       rev_old,
            "torev":         rev_id,
            "format":        "json",
            "formatversion": "2",
        },
        timeout=(10, 30),
    )
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        return None, None, data["error"].get("info", str(data["error"]))

    html = data.get("compare", {}).get("body", "")
    soup = BeautifulSoup(html, "html.parser")

    removed_text = _extract_changed_text(
        soup.find_all("td", class_="diff-deletedline"), "del"
    )
    added_text = _extract_changed_text(
        soup.find_all("td", class_="diff-addedline"), "ins"
    )
    return added_text, removed_text, None


def enrich_pending(
    spark: SparkSession,
    config: Config = CONFIG,
    max_rows: int = 200,
) -> dict:
    """
    Fetch MediaWiki diffs for unenriched triage candidates and write to
    silver.edit_diffs; then fill embeddings with ai_query.

    Parameters
    ----------
    spark:
        Active SparkSession.
    config:
        Runtime configuration.
    max_rows:
        Maximum number of edits to process in one run.

    Returns
    -------
    dict
        Summary with keys: selected, fetched, errors, embedded.
    """
    queue = spark.table(config.gold_triage_table)

    pending = queue
    if spark.catalog.tableExists(config.silver_edit_diffs_table):
        already = spark.table(config.silver_edit_diffs_table).select("wiki", "rev_id")
        pending = pending.join(already, on=["wiki", "rev_id"], how="left_anti")

    rows = (
        pending
        .orderBy(col("event_ts").desc())
        .limit(max_rows)
        .select("wiki", "rev_id", "rev_old", "lang")
        .collect()
    )

    n_selected = len(rows)
    n_fetched  = 0
    n_errors   = 0
    records    = []

    session = session_with_retries(config.contact_email)

    for row in rows:
        rev_id = int(row.rev_id)
        added_text = removed_text = error_msg = None

        try:
            if row.rev_old is None:
                error_msg = "no parent revision (rev_old is null)"
            else:
                added_text, removed_text, error_msg = _fetch_diff(
                    session, row.lang, int(row.rev_old), rev_id
                )
        except Exception as exc:
            error_msg = str(exc)[:500]
            log.warning("rev_id=%d wiki=%s diff fetch failed: %s", rev_id, row.wiki, exc)

        if error_msg:
            n_errors += 1
        else:
            n_fetched += 1

        records.append({
            "wiki":          row.wiki,
            "rev_id":        rev_id,
            "lang":          row.lang,
            "added_text":    added_text,
            "removed_text":  removed_text,
            "added_chars":   len(added_text)   if added_text   is not None else 0,
            "removed_chars": len(removed_text) if removed_text is not None else 0,
            "upper_ratio":   _upper_ratio(added_text),
            "url_count":     _url_count(added_text),
            "diff_text":     _diff_text(added_text, removed_text),
            "embedding":     None,
            "error":         error_msg,
        })

    n_to_embed = sum(1 for r in records if r["diff_text"] is not None)

    if records:
        new_df = (
            spark.createDataFrame(records, schema=_DIFFS_SCHEMA)
            .withColumn("fetched_at", current_timestamp())
        )
        new_df = new_df.withColumn(
            "embedding",
            expr(
                f"CASE WHEN diff_text IS NOT NULL "
                f"THEN ai_query('{config.embedding_endpoint}', diff_text) END"
            ),
        )
        (
            new_df
            .write.format("delta").mode("append")
            .saveAsTable(config.silver_edit_diffs_table)
        )

    return {
        "selected": n_selected,
        "fetched":  n_fetched,
        "errors":   n_errors,
        "embedded": n_to_embed,
    }
