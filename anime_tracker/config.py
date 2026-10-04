from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    redirect_uri: str = "http://localhost:8000/callback"
    token_file: Path = BASE_DIR / "data" / "token.json"
    database_file: Path = BASE_DIR / "data" / "synccore.db"
    log_file: Path = BASE_DIR / "data" / "synccore.log"
    api_url: str = "https://graphql.anilist.co"
    token_url: str = "https://anilist.co/api/v2/oauth/token"
    auth_url: str = "https://anilist.co/api/v2/oauth/authorize"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            client_id=os.getenv("ANILIST_CLIENT_ID", "").strip(),
            client_secret=os.getenv("ANILIST_CLIENT_SECRET", "").strip(),
            redirect_uri=os.getenv("ANILIST_REDIRECT_URI", "http://localhost:8000/callback").strip(),
            database_file=Path(os.getenv("SYNCCORE_DATABASE_FILE", str(BASE_DIR / "data" / "synccore.db"))).expanduser(),
            log_file=Path(os.getenv("SYNCCORE_LOG_FILE", str(BASE_DIR / "data" / "synccore.log"))).expanduser(),
        )

    def validate_credentials(self) -> None:
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "AniList credentials are not configured. Copy .env.example to .env "
                "and set ANILIST_CLIENT_ID and ANILIST_CLIENT_SECRET."
            )

    def ensure_data_dir(self) -> None:
        self.database_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
