"""
WikiGuard job deployment helpers.

Importable from notebooks (``sys.path`` is configured by
``%run ./_bootstrap``) so deployments can be triggered from the workspace
without needing ``scripts/`` on ``sys.path``.  The same functions are also
called by ``scripts/deploy_jobs.py`` for local CLI use.

All job definitions are JSON files whose top-level ``name`` field is used to
find an existing job and reset it in-place, keeping job IDs stable across
updates.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import JobSettings

# ---------------------------------------------------------------------------
# Paths derived from this file's location so callers never need hard-coded
# paths.  jobs.py lives at src/wikiguard/common/jobs.py, four levels below the
# project root.
# ---------------------------------------------------------------------------
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent.parent
JOBS_DIR: Path = _PROJECT_ROOT / "jobs"


def _force_paused(definition: dict) -> None:
    """Force ``pause_status: PAUSED`` on whichever trigger block is present.

    Applied to every deploy so jobs always start paused regardless of what
    the JSON file on disk says.  The caller must resume the job deliberately
    via the Jobs UI or the CLI.
    """
    for key in ("continuous", "schedule", "trigger"):
        if key in definition:
            definition[key]["pause_status"] = "PAUSED"
            break


def upsert_job(
    json_path: str | Path,
    client: Optional[WorkspaceClient] = None,
) -> Tuple[str, int, str]:
    """
    Create or update a Databricks job from a JSON definition file.

    If a job whose ``name`` matches the ``name`` field in the JSON already
    exists it is reset (updated in-place) so its job ID remains stable.
    Otherwise a new job is created.

    Parameters
    ----------
    json_path:
        Path to the job definition JSON file.  Must contain a top-level
        ``name`` field.
    client:
        Optional pre-built ``WorkspaceClient``.  If ``None``, a new one is
        created (authenticates as the current user inside a notebook, or via
        the active Databricks CLI profile locally).

    Returns
    -------
    tuple[str, int, str]
        ``(job_name, job_id, action)`` where *action* is ``"created"`` or
        ``"updated"``.
    """
    path = Path(json_path)
    definition: dict = json.loads(path.read_text(encoding="utf-8"))
    job_name: str = definition["name"]

    wc = client or WorkspaceClient()
    existing = {j.settings.name: j for j in wc.jobs.list()}

    _force_paused(definition)
    settings = JobSettings.from_dict(definition)
    if job_name in existing:
        job_id: int = existing[job_name].job_id
        wc.jobs.reset(job_id=job_id, new_settings=settings)
        return job_name, job_id, "updated"
    else:
        kw = {k: v for k, v in vars(settings).items() if v is not None}
        job = wc.jobs.create(**kw)
        return job_name, job.job_id, "created"


def deploy_all(
    jobs_dir: Path = JOBS_DIR,
    client: Optional[WorkspaceClient] = None,
) -> List[Tuple[str, int, str]]:
    """
    Deploy every ``*.json`` file in *jobs_dir* in sorted order.

    A single ``WorkspaceClient`` is shared across all files to avoid
    re-authenticating for each job.

    Parameters
    ----------
    jobs_dir:
        Directory containing job definition JSON files.
        Defaults to the ``jobs/`` folder at the project root.
    client:
        Optional pre-built ``WorkspaceClient``.

    Returns
    -------
    list[tuple[str, int, str]]
        ``[(job_name, job_id, action), ...]`` for every file deployed.
    """
    json_files = sorted(jobs_dir.glob("*.json"))
    if not json_files:
        return []

    wc = client or WorkspaceClient()
    return [upsert_job(f, client=wc) for f in json_files]
