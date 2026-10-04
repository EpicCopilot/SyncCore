from anime_tracker.reconciliation import map_status, reconcile_status


def test_status_mapping():
    assert map_status("CURRENT") == "Watching"
    assert map_status("COMPLETED") == "Completed"
    assert map_status("PLANNING") == "Plan to Watch"


def test_completed_with_incomplete_progress_becomes_watching():
    assert reconcile_status("COMPLETED", 5, 12) == "Watching"


def test_completed_with_matching_progress_stays_completed():
    assert reconcile_status("COMPLETED", 12, 12) == "Completed"


def test_unknown_total_does_not_use_magic_episode_count():
    assert reconcile_status("COMPLETED", 5, None) == "Completed"


def test_progress_can_override_non_terminal_status():
    assert reconcile_status("PLANNING", 3, 12) == "Watching"


def test_invalid_status_falls_back_to_plan_to_watch():
    assert reconcile_status("UNKNOWN", 0, None) == "Plan to Watch"


def test_zero_progress_does_not_change_on_hold():
    assert reconcile_status("PAUSED", 0, 12) == "On Hold"


def test_sync_service_reconciles_upstream_state(tmp_path):
    from anime_tracker.models import Anime
    from anime_tracker.repository import AnimeRepository
    from anime_tracker.sync_service import SyncService

    class FakeClient:
        def fetch_user_list(self):
            return [{
                "anilist_id": 10,
                "name": "Current Anime",
                "total_episodes": 12,
                "watched_episodes": 4,
                "status": "CURRENT",
                "rating": 8,
                "thumbnail": "",
            }]

    repo = AnimeRepository(tmp_path / "sync.db")
    repo.upsert(Anime(anilist_id=10, name="Old Current"))
    repo.upsert(Anime(anilist_id=20, name="Stale Anime"))
    repo.upsert(Anime(name="Manual Anime"))

    result = SyncService(FakeClient(), repo).sync_user_list()

    assert result.fetched == 1
    assert result.removed == 1
    rows = repo.list_all()
    assert {row["anilist_id"] for row in rows if row["anilist_id"] is not None} == {10}
    assert any(row["name"] == "Manual Anime" for row in rows)
    repo.close()
