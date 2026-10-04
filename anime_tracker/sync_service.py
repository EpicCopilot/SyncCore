from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .anilist_client import AniListClient
from .models import Anime
from .reconciliation import reconcile_status
from .repository import AnimeRepository


@dataclass(frozen=True)
class SyncResult:
    fetched: int
    removed: int


class SyncService:
    """Application service coordinating one complete upstream reconciliation."""

    def __init__(self, client: AniListClient, repository: AnimeRepository):
        self.client = client
        self.repository = repository

    def sync_user_list(self) -> SyncResult:
        entries = self.client.fetch_user_list()
        records: list[Anime] = []

        for entry in entries:
            anilist_id = entry.get("anilist_id")
            if anilist_id is None:
                raise ValueError("AniList returned a record without an ID.")

            total = entry.get("total_episodes")
            progress = int(entry.get("watched_episodes") or 0)
            status = reconcile_status(entry.get("status", "PLANNING"), progress, total)

            records.append(
                Anime(
                    anilist_id=int(anilist_id),
                    name=str(entry.get("name") or "Unknown").strip() or "Unknown",
                    seasons=1,
                    total_episodes=int(total) if total is not None else None,
                    watched_episodes=progress,
                    status=status,
                    rating=float(entry.get("rating") or 0),
                    thumbnail=str(entry.get("thumbnail") or ""),
                )
            )

        fetched, removed = self.repository.synchronize_anilist(records)
        return SyncResult(fetched=fetched, removed=removed)
