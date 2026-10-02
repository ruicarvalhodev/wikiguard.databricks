# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Install dependencies
# MAGIC %pip install "psycopg[binary]" databricks-openai -q

# COMMAND ----------

# DBTITLE 1,Bootstrap
# MAGIC %run ./_bootstrap

# COMMAND ----------

# DBTITLE 1,chat() helper
import json
from wikiguard.agent.audit import new_session
from wikiguard.agent.runner import run_agent

_history: list[dict] = []
_session_id: str = new_session()


def chat(text: str) -> None:
    """Send one turn to the agent, print the tools called and the reply."""
    global _history, _session_id
    _history.append({"role": "user", "content": text})

    result = run_agent(_history, session_id=_session_id)
    _session_id = result["session_id"]
    _history = result["messages"]

    if result["tool_calls"]:
        print("Tools:")
        for tc in result["tool_calls"]:
            tag = "[W]" if tc["is_write"] else "[R]"
            ok  = "ok" if tc["ok"] else "FAILED"
            # First three args give enough context without flooding the output
            preview = dict(list(tc["arguments"].items())[:3])
            print(f"  {tag} {tc['name']}({json.dumps(preview, default=str)}) → {ok}")
        print()

    print(f"Agent: {result['reply']}")
    print()

# COMMAND ----------

# MAGIC %md
# MAGIC ## CDF Test

# COMMAND ----------

chat("Assign case 7326 to Bruno.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## ------

# COMMAND ----------

chat("Escalate all open cases from ~2026-52775-97 and assign them to Ana, with a note explaining why.")

# COMMAND ----------

chat("Yes, go ahead.")

# COMMAND ----------

chat("Option 1: resolve all five and assign them to Ana.")

# COMMAND ----------

chat("Find open tier B cases with risk below 0.2 and show me what dismissing them would change.")

# COMMAND ----------

chat("Assign case 4091 to Zé.")

# COMMAND ----------

chat("What's the status of case 99999999?")

# COMMAND ----------

# MAGIC %md
# MAGIC ## First test

# COMMAND ----------

# DBTITLE 1,Turn 1 — queue overview
chat("How is the review queue looking? Give me an overview and the top 5 open cases.")

# COMMAND ----------

# DBTITLE 1,Turn 2 — case detail
chat("Tell me everything about the first case you listed.")

# COMMAND ----------

# DBTITLE 1,Turn 3 — editor history
chat("Look up that editor's recent edit history.")

# COMMAND ----------

# DBTITLE 1,Turn 4 — page context
chat("Check the current state of that Wikipedia page — has the edit been reverted?")

# COMMAND ----------

# DBTITLE 1,Turn 5 — status update
chat(
    "The edit has been reverted by the community. "
    "Please resolve the case with that as the reason."
)

# COMMAND ----------

# DBTITLE 1,Turn 6 — similar cases
chat("Are there any cases in the queue with similar diff content?")

# COMMAND ----------

# DBTITLE 1,Turn 7 — bulk dismiss preview
chat(
    "Find all open tier-A cases with revert risk below 0.3. "
    "These are likely false positives — I want to dismiss them. Show me what would change first."
)

# COMMAND ----------

# DBTITLE 1,Turn 8 — bulk dismiss confirm
chat("Yes, go ahead and dismiss them.")

# COMMAND ----------

# DBTITLE 1,Final — agent_actions for this session
import uuid
from psycopg.rows import dict_row
from wikiguard.lakebase.client import connect

print(f"Session: {_session_id}\n")

with connect() as conn:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT tool_name, is_write, latency_ms,
                   LEFT(tool_output::text, 120) AS output_preview
            FROM agent_actions
            WHERE session_id = %s
            ORDER BY created_at
            """,
            (uuid.UUID(_session_id),),
        )
        rows = cur.fetchall()

for row in rows:
    tag = "[W]" if row["is_write"] else "[R]"
    print(f"{tag} {row['tool_name']:30s}  {row['latency_ms']:5d} ms  {row['output_preview']}")

print(f"\n{len(rows)} tool call(s) logged this session.")