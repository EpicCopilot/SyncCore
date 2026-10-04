from __future__ import annotations

from typing import Optional

from .models import STATUSES

ANILIST_STATUS_MAP = {
    "CURRENT": "Watching",
    "PLANNING": "Plan to Watch",
    "PAUSED": "On Hold",
    "DROPPED": "Dropped",
    "COMPLETED": "Completed",
}


def map_status(status: str) -> str:
    return ANILIST_STATUS_MAP.get(status, status)


def reconcile_status(status: str, progress: int, total_episodes: Optional[int]) -> str:
    """Resolve obvious status/progress conflicts without inventing episode totals."""
    mapped = map_status(status)
    progress = max(0, int(progress or 0))

    if mapped == "Completed" and total_episodes is not None and progress < total_episodes:
        return "Watching"

    if progress > 0 and mapped not in {"Watching", "Completed"}:
        if total_episodes is None or progress < total_episodes:
            return "Watching"

    return mapped if mapped in STATUSES else "Plan to Watch"
