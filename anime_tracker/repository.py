from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from .exceptions import DatabaseError
from .models import Anime

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS anime (
    id INTEGER PRIMARY KEY,
    anilist_id INTEGER UNIQUE,
    name TEXT NOT NULL,
    seasons INTEGER NOT NULL DEFAULT 1,
    total_episodes INTEGER,
    watched_episodes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Plan to Watch',
    rating REAL NOT NULL DEFAULT 0,
    thumbnail TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_anime_name ON anime(name);
CREATE INDEX IF NOT EXISTS idx_anime_status ON anime(status);
CREATE INDEX IF NOT EXISTS idx_anime_anilist_id ON anime(anilist_id);
"""


class AnimeRepository:
    """SQLite persistence layer safe to use from multiple Qt threads.

    Each operation gets its own short-lived connection. Synchronization can also
    execute a whole upstream reconciliation in one transaction, preventing a
    partially applied sync if any write fails.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        try:
            with self._connect() as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(anime)").fetchall()}
                if columns and "watched_episodes" not in columns:
                    self._migrate_legacy_schema(conn)
                conn.executescript(SCHEMA)
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if version < SCHEMA_VERSION:
                    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise DatabaseError(f"Database initialization failed: {exc}") from exc

    @staticmethod
    def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE anime RENAME TO anime_legacy")
        conn.execute(
            """
            CREATE TABLE anime (
                id INTEGER PRIMARY KEY,
                anilist_id INTEGER UNIQUE,
                name TEXT NOT NULL,
                seasons INTEGER NOT NULL DEFAULT 1,
                total_episodes INTEGER,
                watched_episodes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'Plan to Watch',
                rating REAL NOT NULL DEFAULT 0,
                thumbnail TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO anime
                (id, anilist_id, name, seasons, watched_episodes, status, rating, thumbnail)
            SELECT id, anilist_id, COALESCE(name, 'Unknown'), COALESCE(seasons, 1),
                   COALESCE(episodes, 0), COALESCE(status, 'Plan to Watch'),
                   COALESCE(rating, 0), COALESCE(thumbnail, '')
            FROM anime_legacy
            """
        )
        conn.execute("DROP TABLE anime_legacy")

    def close(self) -> None:
        # Connections are operation-scoped, so there is no shared connection.
        return None

    def list_all(self, order_by: str = "id ASC") -> list[sqlite3.Row]:
        allowed = {
            "id ASC": "id ASC",
            "id DESC": "id DESC",
            "name ASC": "name COLLATE NOCASE ASC",
            "status": "CASE status WHEN 'Watching' THEN 1 WHEN 'Plan to Watch' THEN 2 WHEN 'On Hold' THEN 3 WHEN 'Dropped' THEN 4 WHEN 'Completed' THEN 5 ELSE 6 END, name COLLATE NOCASE ASC",
            "rating": "rating DESC, name COLLATE NOCASE ASC",
        }
        sql_order = allowed.get(order_by, allowed["id ASC"])
        try:
            with self._connect() as conn:
                return conn.execute(f"SELECT * FROM anime ORDER BY {sql_order}").fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not read anime: {exc}") from exc

    @staticmethod
    def _upsert_sql() -> str:
        return """
            INSERT INTO anime
                (anilist_id, name, seasons, total_episodes, watched_episodes, status, rating, thumbnail, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(anilist_id) DO UPDATE SET
                name=excluded.name,
                seasons=excluded.seasons,
                total_episodes=excluded.total_episodes,
                watched_episodes=excluded.watched_episodes,
                status=excluded.status,
                rating=excluded.rating,
                thumbnail=excluded.thumbnail,
                updated_at=CURRENT_TIMESTAMP
        """

    @staticmethod
    def _anime_values(anime: Anime) -> tuple:
        return (
            anime.anilist_id,
            anime.name,
            anime.seasons,
            anime.total_episodes,
            anime.watched_episodes,
            anime.status,
            anime.rating,
            anime.thumbnail,
        )

    def upsert(self, anime: Anime) -> int:
        anime.validate()
        try:
            with self._connect() as conn:
                if anime.anilist_id is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO anime
                            (name, seasons, total_episodes, watched_episodes, status, rating, thumbnail, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """,
                        (
                            anime.name,
                            anime.seasons,
                            anime.total_episodes,
                            anime.watched_episodes,
                            anime.status,
                            anime.rating,
                            anime.thumbnail,
                        ),
                    )
                    return int(cursor.lastrowid)

                conn.execute(self._upsert_sql(), self._anime_values(anime))
                row = conn.execute("SELECT id FROM anime WHERE anilist_id=?", (anime.anilist_id,)).fetchone()
                return int(row[0])
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not save anime: {exc}") from exc

    def synchronize_anilist(self, anime_records: Sequence[Anime]) -> tuple[int, int]:
        """Atomically upsert current AniList records and remove stale linked rows.

        Returns ``(fetched_count, removed_count)``. Manual rows with a NULL
        AniList ID are never removed by this operation.
        """
        records = list(anime_records)
        for anime in records:
            anime.validate()
            if anime.anilist_id is None:
                raise ValueError("AniList synchronization requires anilist_id for every record.")

        current_ids = {int(anime.anilist_id) for anime in records}
        try:
            with self._connect() as conn:
                upsert_sql = self._upsert_sql()
                for anime in records:
                    conn.execute(upsert_sql, self._anime_values(anime))

                if current_ids:
                    placeholders = ",".join("?" for _ in current_ids)
                    cursor = conn.execute(
                        f"DELETE FROM anime WHERE anilist_id IS NOT NULL AND anilist_id NOT IN ({placeholders})",
                        tuple(current_ids),
                    )
                else:
                    cursor = conn.execute("DELETE FROM anime WHERE anilist_id IS NOT NULL")

                removed = max(cursor.rowcount, 0)
                return len(records), removed
        except sqlite3.Error as exc:
            raise DatabaseError(f"AniList synchronization failed and was rolled back: {exc}") from exc

    def update_field(self, db_id: int, field: str, value) -> None:
        allowed = {"name", "seasons", "watched_episodes", "status", "rating"}
        if field not in allowed:
            raise ValueError(f"Unsupported field: {field}")
        try:
            with self._connect() as conn:
                conn.execute(f"UPDATE anime SET {field}=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (value, db_id))
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not update anime: {exc}") from exc

    def delete(self, db_id: int) -> None:
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM anime WHERE id=?", (db_id,))
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not delete anime: {exc}") from exc

    def delete_missing_anilist_ids(self, current_ids: set[int]) -> int:
        """Backward-compatible direct reconciliation operation."""
        try:
            with self._connect() as conn:
                if not current_ids:
                    cursor = conn.execute("DELETE FROM anime WHERE anilist_id IS NOT NULL")
                else:
                    placeholders = ",".join("?" for _ in current_ids)
                    cursor = conn.execute(
                        f"DELETE FROM anime WHERE anilist_id IS NOT NULL AND anilist_id NOT IN ({placeholders})",
                        tuple(current_ids),
                    )
                return max(cursor.rowcount, 0)
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not remove stale AniList records: {exc}") from exc

    def count(self) -> int:
        try:
            with self._connect() as conn:
                return int(conn.execute("SELECT COUNT(*) FROM anime").fetchone()[0])
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not count anime: {exc}") from exc
