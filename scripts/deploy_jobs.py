"""
Thin CLI wrapper around :mod:`wikiguard.common.jobs`.

Run from the project root (or any directory):

    python scripts/deploy_jobs.py                       # deploy all jobs
    python scripts/deploy_jobs.py jobs/ingest.json      # deploy one job

Authentication uses the active Databricks CLI profile.  Inside the workspace,
use ``notebooks/01_deploy_jobs`` instead (no CLI required).
"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/ is not on sys.path; resolve src/ relative to this file so the
# wikiguard package is importable without installation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wikiguard.common.jobs import JOBS_DIR, deploy_all, upsert_job  # noqa: E402


def _print_result(job_name: str, job_id: int, action: str) -> None:
    print(f"[deploy] {action} '{job_name}' (id={job_id})")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            _print_result(*upsert_job(arg))
    else:
        results = deploy_all()
        if not results:
            print(f"[deploy] no job definitions found in {JOBS_DIR}")
        for row in results:
            _print_result(*row)
