from pathlib import Path

from anime_tracker.models import Anime
from anime_tracker.repository import AnimeRepository


def test_upsert_is_idempotent(tmp_path: Path):
    repo = AnimeRepository(tmp_path / "test.db")
    anime = Anime(anilist_id=123, name="Example Anime", total_episodes=12, watched_episodes=3, status="Watching", rating=8.0)
    repo.upsert(anime)
    repo.upsert(anime)
    assert repo.count() == 1

    updated = Anime(anilist_id=123, name="Example Anime", total_episodes=12, watched_episodes=7, status="Watching", rating=9.0)
    repo.upsert(updated)
    rows = repo.list_all()
    assert len(rows) == 1
    assert rows[0]["watched_episodes"] == 7
    assert rows[0]["rating"] == 9.0
    repo.close()


def test_repository_can_be_used_from_multiple_threads(tmp_path: Path):
    from concurrent.futures import ThreadPoolExecutor

    repo = AnimeRepository(tmp_path / "threaded.db")

    def write(i: int):
        repo.upsert(Anime(anilist_id=1000 + i, name=f"Anime {i}", total_episodes=12, watched_episodes=i, status="Watching"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))

    assert repo.count() == 12
    repo.close()


def test_delete_missing_anilist_ids_removes_stale_records_but_keeps_manual(tmp_path: Path):
    repo = AnimeRepository(tmp_path / "reconcile.db")
    repo.upsert(Anime(anilist_id=1, name="Current A"))
    repo.upsert(Anime(anilist_id=2, name="Stale B"))
    repo.upsert(Anime(anilist_id=3, name="Current C"))
    repo.upsert(Anime(name="Manual Anime"))

    removed = repo.delete_missing_anilist_ids({1, 3})

    assert removed == 1
    rows = repo.list_all()
    assert {row["anilist_id"] for row in rows if row["anilist_id"] is not None} == {1, 3}
    assert any(row["name"] == "Manual Anime" and row["anilist_id"] is None for row in rows)
    repo.close()


def test_delete_missing_anilist_ids_can_clear_all_upstream_records(tmp_path: Path):
    repo = AnimeRepository(tmp_path / "empty_reconcile.db")
    repo.upsert(Anime(anilist_id=1, name="Old A"))
    repo.upsert(Anime(anilist_id=2, name="Old B"))
    repo.upsert(Anime(name="Manual Anime"))

    removed = repo.delete_missing_anilist_ids(set())

    assert removed == 2
    rows = repo.list_all()
    assert len(rows) == 1
    assert rows[0]["name"] == "Manual Anime"
    repo.close()


def test_atomic_sync_updates_and_removes_in_one_operation(tmp_path: Path):
    repo = AnimeRepository(tmp_path / "atomic.db")
    repo.upsert(Anime(anilist_id=1, name="Old Name", watched_episodes=1))
    repo.upsert(Anime(anilist_id=2, name="Stale"))
    repo.upsert(Anime(name="Manual"))

    fetched, removed = repo.synchronize_anilist([
        Anime(anilist_id=1, name="New Name", watched_episodes=4, status="Watching"),
        Anime(anilist_id=3, name="New Anime"),
    ])

    assert fetched == 2
    assert removed == 1
    rows = repo.list_all()
    linked = {row["anilist_id"]: row for row in rows if row["anilist_id"] is not None}
    assert set(linked) == {1, 3}
    assert linked[1]["name"] == "New Name"
    assert linked[1]["watched_episodes"] == 4
    assert any(row["name"] == "Manual" for row in rows)
    repo.close()


def test_database_schema_version_is_current(tmp_path: Path):
    repo = AnimeRepository(tmp_path / "version.db")
    import sqlite3
    with sqlite3.connect(tmp_path / "version.db") as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    repo.close()


def test_legacy_schema_is_migrated(tmp_path: Path):
    import sqlite3

    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE anime (
                id INTEGER PRIMARY KEY,
                anilist_id INTEGER UNIQUE,
                name TEXT,
                seasons INTEGER,
                episodes INTEGER,
                status TEXT,
                rating REAL,
                thumbnail TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO anime (id, anilist_id, name, seasons, episodes, status, rating, thumbnail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (7, 700, "Legacy Anime", 1, 5, "Watching", 8.0, ""),
        )

    repo = AnimeRepository(db)
    row = repo.list_all()[0]
    assert row["id"] == 7
    assert row["anilist_id"] == 700
    assert row["watched_episodes"] == 5
    assert row["total_episodes"] is None
    repo.close()


def test_atomic_sync_validates_before_writing(tmp_path: Path):
    repo = AnimeRepository(tmp_path / "rollback.db")
    repo.upsert(Anime(anilist_id=1, name="Original"))

    invalid = Anime(anilist_id=2, name="")
    try:
        repo.synchronize_anilist([Anime(anilist_id=1, name="Would Change"), invalid])
        assert False, "Expected validation failure"
    except ValueError:
        pass

    row = repo.list_all()[0]
    assert row["name"] == "Original"
    repo.close()
