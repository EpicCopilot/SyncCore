from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

STATUSES = ["Watching", "Completed", "On Hold", "Dropped", "Plan to Watch"]


@dataclass(frozen=True)
class Anime:
    name: str
    seasons: int = 1
    watched_episodes: int = 0
    status: str = "Plan to Watch"
    rating: float = 0.0
    thumbnail: str = ""
    anilist_id: Optional[int] = None
    total_episodes: Optional[int] = None
    db_id: Optional[int] = None

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("Anime name cannot be empty.")
        if self.seasons < 1:
            raise ValueError("Seasons must be at least 1.")
        if self.watched_episodes < 0:
            raise ValueError("Watched episodes cannot be negative.")
        if self.total_episodes is not None and self.total_episodes < 0:
            raise ValueError("Total episodes cannot be negative.")
        if not 0 <= self.rating <= 10:
            raise ValueError("Rating must be between 0 and 10.")
        if self.status not in STATUSES:
            raise ValueError(f"Invalid status: {self.status}")
