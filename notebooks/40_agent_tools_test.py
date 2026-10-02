# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Install psycopg (must precede bootstrap — %pip restarts Python)
# MAGIC %pip install "psycopg[binary]" -q

# COMMAND ----------

# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,Start session and list tools
import json

from wikiguard.agent.audit import new_session
from wikiguard.agent.registry import TOOLS

session = new_session()
print(f"Session: {session}")
print(f"Tools ({len(TOOLS)}): {list(TOOLS.keys())}")

# COMMAND ----------

# DBTITLE 1,search_cases
from wikiguard.agent.tools_read import search_cases

result = search_cases(limit=5)
print(json.dumps(result, indent=2, default=str))

# COMMAND ----------

# DBTITLE 1,get_case_detail
from wikiguard.agent.tools_read import get_case_detail

cases = search_cases(limit=1)
if cases["ok"] and cases["count"] > 0:
    cid = cases["cases"][0]["case_id"]
    result = get_case_detail(cid)
    print(json.dumps(result, indent=2, default=str))
else:
    print("No cases found")

# COMMAND ----------

# DBTITLE 1,get_editor_history
from wikiguard.agent.tools_read import get_editor_history

cases = search_cases(limit=1)
if cases["ok"] and cases["count"] > 0:
    editor = cases["cases"][0].get("editor")
    if editor:
        result = get_editor_history(editor, limit=5)
        print(json.dumps(result, indent=2, default=str))
    else:
        print("First case has no editor")
else:
    print("No cases found")

# COMMAND ----------

# DBTITLE 1,get_page_context
from wikiguard.agent.tools_read import get_page_context

cases = search_cases(limit=1)
if cases["ok"] and cases["count"] > 0:
    c = cases["cases"][0]
    result = get_page_context(c["wiki"], c["page_title"])
    print(json.dumps(result, indent=2, default=str))
else:
    print("No cases found")

# COMMAND ----------

# DBTITLE 1,query_triage_metrics
from wikiguard.agent.tools_read import query_triage_metrics

result = query_triage_metrics()
print(json.dumps(result, indent=2, default=str))

# COMMAND ----------

# DBTITLE 1,find_similar_cases
from wikiguard.agent.tools_read import find_similar_cases

cases = search_cases(limit=1)
if cases["ok"] and cases["count"] > 0:
    cid = cases["cases"][0]["case_id"]
    result = find_similar_cases(cid, k=3)
    print(json.dumps(result, indent=2, default=str))
else:
    print("No cases found")

# COMMAND ----------

# DBTITLE 1,assign_case (success + failure)
from wikiguard.agent.tools_write import assign_case

cases = search_cases(limit=1)
if cases["ok"] and cases["count"] > 0:
    cid = cases["cases"][0]["case_id"]

    # Success: assign to Clara
    result = assign_case(cid, "Clara")
    print("Success:")
    print(json.dumps(result, indent=2, default=str))

    # Failure: unknown reviewer
    result = assign_case(cid, "Nobody")
    print("\nFailure (unknown reviewer):")
    print(json.dumps(result, indent=2, default=str))
else:
    print("No cases found")

# COMMAND ----------

# DBTITLE 1,update_case_status (success + failures)
from wikiguard.agent.tools_write import update_case_status

cases = search_cases(limit=1)
if cases["ok"] and cases["count"] > 0:
    cid = cases["cases"][0]["case_id"]

    # Success: escalate
    result = update_case_status(cid, "escalated", "High-risk edit needs supervisor review")
    print("Success:")
    print(json.dumps(result, indent=2, default=str))

    # Failure: invalid status
    result = update_case_status(cid, "closed", "test")
    print("\nFailure (invalid status):")
    print(json.dumps(result, indent=2, default=str))

    # Failure: empty reason
    result = update_case_status(cid, "resolved", "")
    print("\nFailure (empty reason):")
    print(json.dumps(result, indent=2, default=str))

    # Failure: same status
    result = update_case_status(cid, "escalated", "test")
    print("\nFailure (same status):")
    print(json.dumps(result, indent=2, default=str))
else:
    print("No cases found")

# COMMAND ----------

# DBTITLE 1,add_case_note (success + failure)
from wikiguard.agent.tools_write import add_case_note

cases = search_cases(limit=1)
if cases["ok"] and cases["count"] > 0:
    cid = cases["cases"][0]["case_id"]

    # Success
    result = add_case_note(cid, "Reviewed the diff — looks like a test edit.")
    print("Success:")
    print(json.dumps(result, indent=2, default=str))

    # Failure: empty body
    result = add_case_note(cid, "")
    print("\nFailure (empty body):")
    print(json.dumps(result, indent=2, default=str))
else:
    print("No cases found")

# COMMAND ----------

# DBTITLE 1,create_watchlist
from wikiguard.agent.tools_write import create_watchlist

result = create_watchlist("Ana", "Japan vandalism watch", [
    {"wiki": "enwiki", "page_title": "Test page"},
    {"wiki": "enwiki", "page_title": "Another test page"},
])
print(json.dumps(result, indent=2, default=str))

# COMMAND ----------

# DBTITLE 1,bulk_dismiss (preview + confirm)
from wikiguard.agent.tools_write import bulk_dismiss

cases = search_cases(status="open", limit=3)
if cases["ok"] and cases["count"] > 0:
    ids = [c["case_id"] for c in cases["cases"]]

    # Preview (confirm=False)
    result = bulk_dismiss(ids, "Bulk dismiss test — low priority edits", confirm=False)
    print("Preview (confirm=False):")
    print(json.dumps(result, indent=2, default=str))

    # Confirm
    result = bulk_dismiss(ids, "Bulk dismiss test — low priority edits", confirm=True)
    print("\nConfirm (confirm=True):")
    print(json.dumps(result, indent=2, default=str))
else:
    print("No open cases found")

# COMMAND ----------

# DBTITLE 1,Audit log — count rows per tool
from psycopg.rows import dict_row
from wikiguard.lakebase.client import connect

with connect() as conn:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT count(*) AS n FROM agent_actions")
        total = cur.fetchone()["n"]

        cur.execute(
            "SELECT tool_name, count(*) AS n, max(latency_ms) AS max_ms "
            "FROM agent_actions GROUP BY tool_name ORDER BY tool_name"
        )
        per_tool = cur.fetchall()

print(f"Total agent_actions rows: {total}")
print(json.dumps(per_tool, indent=2, default=str))