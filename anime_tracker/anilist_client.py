from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .auth import AniListAuth
from .exceptions import ApiError, AuthenticationError

logger = logging.getLogger(__name__)

PROFILE_QUERY = """
query { Viewer { name } }
"""

USER_LIST_QUERY = """
query ($userName: String) {
  MediaListCollection(
    userName: $userName,
    type: ANIME,
    status_in: [CURRENT, PLANNING, PAUSED, DROPPED, COMPLETED]
  ) {
    lists {
      entries {
        media {
          id
          title { romaji english }
          episodes
          coverImage { large }
        }
        status
        score
        progress
      }
    }
  }
}
"""

SEARCH_QUERY = """
query ($search: String) {
  Media(search: $search, type: ANIME) {
    id
    title { romaji english }
    episodes
    coverImage { large }
  }
}
"""


class AniListClient:
    def __init__(self, auth: AniListAuth, api_url: str, timeout: float = 20.0):
        self.auth = auth
        self.api_url = api_url
        self.timeout = timeout

    @staticmethod
    def _configure_retries(session: requests.Session) -> None:
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"POST"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)

    def _post(self, query: str, variables: dict | None = None) -> dict[str, Any]:
        if not self.auth.is_logged_in() or not self.auth.session:
            raise AuthenticationError("AniList session is not available. Please log in.")

        self._configure_retries(self.auth.session)
        try:
            response = self.auth.session.post(
                self.api_url,
                json={"query": query, "variables": variables or {}},
                timeout=self.timeout,
            )
            if response.status_code in (401, 403):
                raise AuthenticationError("AniList authentication was rejected. Please log in again.")
            response.raise_for_status()
            payload = response.json()
        except AuthenticationError:
            raise
        except requests.RequestException as exc:
            raise ApiError(f"AniList request failed: {exc}") from exc
        except ValueError as exc:
            raise ApiError("AniList returned invalid JSON.") from exc

        if payload.get("errors"):
            messages = "; ".join(str(error.get("message", "Unknown GraphQL error")) for error in payload["errors"])
            raise ApiError(messages)

        data = payload.get("data")
        if not isinstance(data, dict):
            raise ApiError("AniList returned an empty or invalid data payload.")
        return data

    def get_username(self) -> str:
        data = self._post(PROFILE_QUERY)
        try:
            return str(data["Viewer"]["name"])
        except (KeyError, TypeError) as exc:
            raise ApiError("AniList profile response did not contain a username.") from exc

    def fetch_user_list(self) -> list[dict[str, Any]]:
        username = self.get_username()
        data = self._post(USER_LIST_QUERY, {"userName": username})
        collection = data.get("MediaListCollection")
        if not isinstance(collection, dict):
            raise ApiError("AniList returned an invalid MediaListCollection payload.")
        lists = collection.get("lists")
        if not isinstance(lists, list):
            raise ApiError("AniList returned an invalid anime-list collection.")

        entries: list[dict[str, Any]] = []
        for media_list in lists:
            if not isinstance(media_list, dict):
                raise ApiError("AniList returned an invalid anime-list entry group.")
            raw_entries = media_list.get("entries")
            if not isinstance(raw_entries, list):
                raise ApiError("AniList returned an invalid anime-list entries payload.")
            for entry in raw_entries:
                if not isinstance(entry, dict):
                    raise ApiError("AniList returned an invalid anime-list record.")
                media = entry.get("media") or {}
                title_data = media.get("title") or {}
                cover = media.get("coverImage") or {}
                entries.append(
                    {
                        "anilist_id": media.get("id"),
                        "name": title_data.get("english") or title_data.get("romaji") or "Unknown",
                        "watched_episodes": int(entry.get("progress") or 0),
                        "total_episodes": media.get("episodes"),
                        "status": entry.get("status", "PLANNING"),
                        "rating": float(entry.get("score") or 0),
                        "thumbnail": cover.get("large") or "",
                    }
                )
        return entries

    def search(self, name: str) -> dict[str, Any] | None:
        if not name.strip():
            return None
        data = self._post(SEARCH_QUERY, {"search": name.strip()})
        media = data.get("Media")
        if not media:
            return None
        title_data = media.get("title") or {}
        cover = media.get("coverImage") or {}
        return {
            "anilist_id": media.get("id"),
            "title": title_data.get("english") or title_data.get("romaji") or "Unknown",
            "episodes": media.get("episodes"),
            "thumbnail": cover.get("large") or "",
        }
