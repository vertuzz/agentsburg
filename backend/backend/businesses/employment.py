"""
Employment management — re-exports from jobs.py and workers.py.

Split into:
  jobs.py    — post_job, list_jobs, apply_job
  workers.py — fire_employee, quit_job, hire_npc_worker
"""

import uuid

from backend.businesses.jobs import apply_job, list_jobs, post_job  # noqa: F401
from backend.businesses.workers import (  # noqa: F401
    fire_employee,
    hire_npc_worker,
    quit_job,
)

# Sentinel agent_id used for NPC worker records — keep here for backwards compat
NPC_WORKER_SENTINEL_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
