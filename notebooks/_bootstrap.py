# Databricks notebook source
# DBTITLE 1,About this notebook
# MAGIC %md
# MAGIC ## WikiGuard Bootstrap
# MAGIC
# MAGIC Discovers the project root and wires `src/` onto `sys.path`.
# MAGIC
# MAGIC **Every notebook starts with `%run ./_bootstrap` as its first cell.**
# MAGIC
# MAGIC See the README section *Root discovery method* for how the root is found, and
# MAGIC *When the code does not update* for `%autoreload 2` limitations.

# COMMAND ----------

# DBTITLE 1,Root discovery and sys.path wiring
import os
import sys


def _log(msg: str) -> None:
    print(f"[bootstrap] {msg}")


def _discover_project_root() -> str:
    """
    Discover the WikiGuard project root using a three-level fallback chain.

    Strategy 1 -- dbutils notebook context (validated for this workspace).
        The bootstrap lives at <root>/notebooks/_bootstrap, so walking up two
        path components yields the project root.

    Strategy 2 -- os.getcwd() walk-up.
        When Workspace Files is active, os.getcwd() returns the notebook's
        directory.  Walk upward until we find src/wikiguard.

    Strategy 3 -- os.getcwd() as-is (last resort).
        Printed with a WARNING so it is immediately visible when wrong.
    """
    # --- Strategy 1: dbutils notebook context ---
    try:
        ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        nb_path: str = ctx.notebookPath().get()
        # nb_path: /Users/user@example.com/wikiguard.databricks/notebooks/_bootstrap
        # Remove the last 2 components to get the project root.
        ws_root = "/Workspace" + "/".join(nb_path.split("/")[:-2])
        if os.path.isdir(os.path.join(ws_root, "src")):
            _log(f"root via strategy 1 (dbutils context): {ws_root}")
            return ws_root
        _log(f"strategy 1 resolved {ws_root} but src/ not found -- falling back")
    except Exception as _exc:
        _log(f"strategy 1 unavailable ({_exc}) -- falling back")

    # --- Strategy 2: os.getcwd() walk-up ---
    candidate = os.getcwd()
    for _ in range(6):
        if os.path.isdir(os.path.join(candidate, "src", "wikiguard")):
            _log(f"root via strategy 2 (cwd walk-up): {candidate}")
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent

    # --- Strategy 3: cwd fallback ---
    cwd = os.getcwd()
    _log(f"WARNING -- root via strategy 3 (cwd fallback, verify manually): {cwd}")
    return cwd


PROJECT_ROOT: str = _discover_project_root()
_SRC: str = os.path.join(PROJECT_ROOT, "src")

if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
    _log(f"added to sys.path: {_SRC}")
else:
    _log(f"already on sys.path: {_SRC}")

# COMMAND ----------

# DBTITLE 1,Enable autoreload
# Enable autoreload so edits to src/ are picked up without a kernel restart.
#
# Limitations -- see README section "When the code does not update":
#   - Does not reliably reload dataclasses / enums with structural changes.
#   - Does not discover new modules that have not been imported yet.
#   - When in doubt: detach and re-attach the cluster.
try:
    from IPython import get_ipython as _get_ipython
    _ip = _get_ipython()
    if _ip is not None:
        _ip.run_line_magic("load_ext", "autoreload")
        _ip.run_line_magic("autoreload", "2")
        _log("autoreload 2 enabled")
    else:
        _log("IPython not available -- autoreload skipped (non-interactive context)")
except Exception as _exc:
    _log(f"autoreload setup failed: {_exc}")