from anime_tracker.models import Anime
from anime_tracker.repository import AnimeRepository
from anime_tracker.sync_service import SyncService


class FakeClient:
    def __init__(self, entries):
        self.entries = entries

    def fetch_user_list(self):
        return self.entries


def test_empty_upstream_list_removes_linked_records_but_preserves_manual(tmp_path):
    repo = AnimeRepository(tmp_path / "empty.db")
    repo.upsert(Anime(anilist_id=1, name="Old"))
    repo.upsert(Anime(name="Manual"))

    result = SyncService(FakeClient([]), repo).sync_user_list()

    assert result.fetched == 0
    assert result.removed == 1
    rows = repo.list_all()
    assert len(rows) == 1
    assert rows[0]["name"] == "Manual"
    repo.close()


def test_sync_is_idempotent(tmp_path):
    entries = [{
        "anilist_id": 10,
        "name": "Example",
        "total_episodes": 12,
        "watched_episodes": 4,
        "status": "CURRENT",
        "rating": 8,
        "thumbnail": "",
    }]
    repo = AnimeRepository(tmp_path / "idempotent.db")
    service = SyncService(FakeClient(entries), repo)

    first = service.sync_user_list()
    second = service.sync_user_list()

    assert first == second
    assert repo.count() == 1
    repo.close()
