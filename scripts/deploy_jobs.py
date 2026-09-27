"""
WikiGuard job deployment script.

Manages Databricks Jobs without Declarative Automation Bundles (DABs).
Job definitions live as JSON files in ``jobs/`` and are created-or-updated
by this script using the Databricks Python SDK.  Keeping definitions in git
preserves the same auditability that DABs would otherwise provide.

Usage
-----
    # Deploy every job defined in jobs/
    python scripts/deploy_jobs.py

    # Deploy a specific job definition
    python scripts/deploy_jobs.py jobs/ingest.json

JSON format
-----------
Each JSON file must be a valid Databricks ``JobSettings`` object.  The
top-level ``name`` field is used to find an existing job with the same name
so the job ID stays stable across updates.

Example skeleton (jobs/ingest.json)::

    {
      "name": "wikiguard-ingest",
      "tasks": [...],
      "job_clusters": [...]
    }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import JobSettings

_JOBS_DIR: Path = Path(__file__).resolve().parent.parent / "jobs"


def upsert_job(json_path: str | Path) -> int:
    """
    Create or update a Databricks job from a JSON definition file.

    If a job whose ``name`` matches the ``name`` field in the JSON already
    exists, it is reset (updated in-place) so its job ID remains stable.
    Otherwise a new job is created.

    Parameters
    ----------
    json_path:
        Path to the job definition JSON file.  Must contain a top-level
        ``name`` field.

    Returns
    -------
    int
        The job ID (new or existing).
    """
    path = Path(json_path)
    definition: dict = json.loads(path.read_text(encoding="utf-8"))
    job_name: str = definition["name"]

    client = WorkspaceClient()

    # Build a name -> job mapping from the current workspace state.
    existing = {j.settings.name: j for j in client.jobs.list()}

    if job_name in existing:
        job_id: int = existing[job_name].job_id
        client.jobs.reset(job_id=job_id, new_settings=JobSettings(**definition))
        print(f"[deploy] updated job '{job_name}' (id={job_id})")
    else:
        job = client.jobs.create(**definition)
        job_id = job.job_id
        print(f"[deploy] created job '{job_name}' (id={job_id})")

    return job_id


def deploy_all(jobs_dir: Path = _JOBS_DIR) -> None:
    """Deploy every ``*.json`` file found in *jobs_dir* in sorted order."""
    json_files = sorted(jobs_dir.glob("*.json"))
    if not json_files:
        print(f"[deploy] no job definitions found in {jobs_dir}")
        return
    for f in json_files:
        upsert_job(f)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            upsert_job(arg)
    else:
        deploy_all()
