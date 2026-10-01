"""
Lakebase schema setup and seed data.

Functions
---------
apply_schema()   -- Applies the full DDL from schema.sql in one transaction.
seed_reviewers() -- Inserts demo reviewer accounts (idempotent).
"""
from __future__ import annotations

import pathlib

from wikiguard.lakebase.client import connect

_SQL_PATH = pathlib.Path(__file__).parent / "schema.sql"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _split_sql(script: str) -> list[str]:
    """
    Split a SQL script into individual statements.

    Handles line comments (--), block comments (/* ... */), single-quoted
    strings (with '' escaping), and dollar-quoted strings ($$ ... $$ or
    $tag$ ... $tag$).  Statements are separated by semicolons that appear
    at the outermost (unquoted) level.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)

    while i < n:
        c = script[i]

        # Line comment: consume to end of line
        if c == "-" and i + 1 < n and script[i + 1] == "-":
            end = script.find("\n", i)
            end = end if end != -1 else n - 1
            buf.append(script[i : end + 1])
            i = end + 1
            continue

        # Block comment: consume to */
        if c == "/" and i + 1 < n and script[i + 1] == "*":
            end = script.find("*/", i + 2)
            end = end if end != -1 else n - 2
            buf.append(script[i : end + 2])
            i = end + 2
            continue

        # Single-quoted string: consume including '' escapes
        if c == "'":
            j = i + 1
            while j < n:
                if script[j] == "'":
                    if j + 1 < n and script[j + 1] == "'":
                        j += 2  # escaped quote
                        continue
                    break
                j += 1
            buf.append(script[i : j + 1])
            i = j + 1
            continue

        # Dollar-quoted string: find matching close tag
        if c == "$":
            tag_end = script.find("$", i + 1)
            if tag_end != -1:
                tag = script[i : tag_end + 1]
                close = script.find(tag, tag_end + 1)
                if close != -1:
                    buf.append(script[i : close + len(tag)])
                    i = close + len(tag)
                    continue

        # Statement terminator
        if c == ";":
            buf.append(c)
            stmt = "".join(buf).strip()
            if stmt and stmt != ";":
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(c)
        i += 1

    # Trailing content without a semicolon
    remaining = "".join(buf).strip()
    if remaining:
        statements.append(remaining)

    return [s for s in statements if s.strip()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_schema() -> None:
    """
    Apply the full Lakebase schema DDL in a single transaction.

    Reads ``schema.sql`` from the same package directory.  Every statement
    is idempotent (``IF NOT EXISTS``, ``CREATE OR REPLACE``, ``ALTER TABLE``),
    so re-running is always safe.
    """
    ddl = _SQL_PATH.read_text()
    statements = _split_sql(ddl)
    with connect() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
    print(f"Schema applied ({len(statements)} statements).")


_REVIEWERS: list[tuple] = [
    ("Ana",   "ana@wikiguard.example",   ["ja", "zh"], "reviewer"),
    ("Bruno", "bruno@wikiguard.example", ["pt", "es"], "reviewer"),
    ("Clara", "clara@wikiguard.example", ["en"],       "reviewer"),
    ("Diogo", "diogo@wikiguard.example", ["fr", "de"], "reviewer"),
]


def seed_reviewers() -> None:
    """
    Insert demo reviewer accounts; no-op for rows that already exist.

    Uses ``ON CONFLICT (email) DO NOTHING`` so re-running never raises
    and never overwrites existing data.
    """
    sql = """
        INSERT INTO reviewers (display_name, email, wiki_focus, role)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (email) DO NOTHING
    """
    with connect() as conn:
        with conn.cursor() as cur:
            for row in _REVIEWERS:
                cur.execute(sql, row)
    print(f"Reviewers seeded ({len(_REVIEWERS)} rows; existing records unchanged).")
