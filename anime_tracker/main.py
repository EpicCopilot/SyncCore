from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtWidgets import QApplication, QMessageBox

from .anilist_client import AniListClient
from .auth import AniListAuth
from .config import Settings
from .repository import AnimeRepository
from .sync_service import SyncService
from .ui import AnimeTracker


def configure_logging(log_file) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def main() -> int:
    settings = Settings.from_env()
    settings.ensure_data_dir()
    configure_logging(settings.log_file)
    logger = logging.getLogger(__name__)

    app = QApplication(sys.argv)
    app.setApplicationName("SyncCore")
    app.setOrganizationName("SyncCore")

    try:
        auth = AniListAuth(settings)
        repository = AnimeRepository(settings.database_file)
        client = AniListClient(auth, settings.api_url)
        service = SyncService(client, repository)
        window = AnimeTracker(repository, client, service, auth)

        if auth.is_logged_in():
            window.fetch_anilist_data()
        else:
            try:
                auth.login()
                window.fetch_anilist_data()
            except Exception as exc:
                logger.warning("AniList login unavailable: %s", exc)
                QMessageBox.information(
                    window,
                    "AniList Not Connected",
                    "SyncCore is available in local mode. Connect an AniList OAuth application "
                    "and use Sync with AniList when authentication is configured.",
                )

        window.show()
        return app.exec()
    except Exception as exc:
        logger.exception("Application startup failed")
        QMessageBox.critical(None, "Startup Error", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
