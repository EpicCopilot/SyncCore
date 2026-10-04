from __future__ import annotations

import logging
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView, QStatusBar,
)

from .anilist_client import AniListClient
from .models import Anime, STATUSES
from .repository import AnimeRepository
from .sync_service import SyncResult, SyncService
from .worker import SyncWorker

logger = logging.getLogger(__name__)


class AddAnimeDialog(QDialog):
    data_ready = Signal(dict)

    def __init__(self, client: AniListClient, parent=None):
        super().__init__(parent)
        self.client = client
        self.anilist_id = None
        self.thumbnail_url = ""
        self.total_episodes = None
        self.network_manager = QNetworkAccessManager(self)
        self.thumbnail_reply: QNetworkReply | None = None
        self.setWindowTitle("Add New Anime")

        layout = QFormLayout()
        self.name_input = QLineEdit()
        self.search_button = QPushButton("Search AniList")
        self.search_button.clicked.connect(self.search_anilist)
        name_row = QHBoxLayout()
        name_row.addWidget(self.name_input)
        name_row.addWidget(self.search_button)
        layout.addRow("Name:", name_row)

        self.seasons_input = QLineEdit("1")
        self.episodes_input = QLineEdit("0")
        self.status_input = QComboBox()
        self.status_input.addItems(STATUSES)
        self.rating_input = QLineEdit("0")
        self.thumbnail_preview = QLabel("Search AniList to preview cover")
        self.thumbnail_preview.setFixedSize(120, 170)
        self.thumbnail_preview.setWordWrap(True)
        self.thumbnail_preview.setAlignment(Qt.AlignCenter)

        layout.addRow("Seasons:", self.seasons_input)
        layout.addRow("Episodes Watched:", self.episodes_input)
        layout.addRow("Status:", self.status_input)
        layout.addRow("Rating:", self.rating_input)
        layout.addRow("Cover Preview:", self.thumbnail_preview)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addLayout(layout)
        root.addWidget(buttons)

    def search_anilist(self):
        try:
            result = self.client.search(self.name_input.text())
        except Exception as exc:
            QMessageBox.critical(self, "AniList Error", str(exc))
            return
        if not result:
            self.anilist_id = None
            self.total_episodes = None
            self.thumbnail_preview.setText("Not Found")
            QMessageBox.warning(self, "Not Found", "Anime not found on AniList.")
            return

        self.anilist_id = result["anilist_id"]
        self.name_input.setText(result["title"])
        self.total_episodes = result.get("episodes")
        self.episodes_input.setPlaceholderText(f"Watched (Total: {self.total_episodes or '?'})")
        self.thumbnail_url = result.get("thumbnail", "")
        self.load_thumbnail(self.thumbnail_url)

    def load_thumbnail(self, url: str):
        if self.thumbnail_reply is not None:
            self.thumbnail_reply.abort()
            self.thumbnail_reply.deleteLater()
            self.thumbnail_reply = None

        self.thumbnail_preview.clear()
        self.thumbnail_preview.setText("Loading preview…")
        if not url:
            self.thumbnail_preview.setText("No cover available")
            return

        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.UserAgentHeader, "SyncCore/1.0")
        self.thumbnail_reply = self.network_manager.get(request)
        self.thumbnail_reply.finished.connect(self._thumbnail_download_finished)

    def _thumbnail_download_finished(self):
        reply = self.thumbnail_reply
        self.thumbnail_reply = None
        if reply is None:
            return

        try:
            if reply.error() != QNetworkReply.NoError:
                logger.warning("Thumbnail load failed: %s", reply.errorString())
                self.thumbnail_preview.setText("Preview unavailable")
                return

            image_data = bytes(reply.readAll())
            pixmap = QPixmap()
            if not pixmap.loadFromData(image_data):
                logger.warning("Thumbnail response was not a supported image")
                self.thumbnail_preview.setText("Preview unavailable")
                return

            self.thumbnail_preview.setPixmap(
                pixmap.scaled(self.thumbnail_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        finally:
            reply.deleteLater()

    def accept(self):
        try:
            data = {
                "anilist_id": self.anilist_id,
                "name": self.name_input.text().strip(),
                "seasons": int(self.seasons_input.text() or 1),
                "watched_episodes": int(self.episodes_input.text() or 0),
                "total_episodes": int(self.total_episodes) if self.total_episodes else None,
                "status": self.status_input.currentText(),
                "rating": float(self.rating_input.text() or 0),
                "thumbnail": self.thumbnail_url,
            }
            Anime(**data).validate()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Invalid Input", str(exc))
            return
        self.data_ready.emit(data)
        super().accept()


class AnimeTracker(QWidget):
    def __init__(self, repository: AnimeRepository, client: AniListClient, sync_service: SyncService, auth):
        super().__init__()
        self.repository = repository
        self.client = client
        self.sync_service = sync_service
        self.auth = auth
        self.thread: QThread | None = None
        self.worker: SyncWorker | None = None
        self.sort_order = "id ASC"
        self.setWindowTitle("SyncCore — Data Synchronization")
        self.setMinimumSize(900, 600)
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        root = QVBoxLayout(self)
        controls = QHBoxLayout()

        self.add_button = QPushButton("➕ Add Anime")
        self.add_button.clicked.connect(self.add_anime)
        self.delete_button = QPushButton("❌ Delete Selected")
        self.delete_button.clicked.connect(self.delete_selected)
        self.refresh_button = QPushButton("Sync with AniList")
        self.refresh_button.clicked.connect(self.fetch_anilist_data)
        self.reconnect_button = QPushButton("Reconnect AniList")
        self.reconnect_button.clicked.connect(self.reconnect_anilist)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search anime...")
        self.search_input.textChanged.connect(self.search_anime)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["DB ID (Ascending)", "DB ID (Descending)", "Alphabetical (A-Z)", "Watching Status", "Rating (High-Low)"])
        self.sort_combo.currentTextChanged.connect(self.change_sort_order)

        for widget in (self.add_button, self.delete_button, self.refresh_button, self.reconnect_button, self.search_input):
            controls.addWidget(widget)
        controls.addWidget(QLabel("Sort By:"))
        controls.addWidget(self.sort_combo)
        root.addLayout(controls)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ID", "Name", "Seasons", "Watched", "Total", "Status", "Rating"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.table.cellChanged.connect(self.save_cell_change)
        root.addWidget(self.table)

        self.status_bar = QStatusBar()
        self.status_bar.showMessage("Ready — local database loaded")
        root.addWidget(self.status_bar)

    def change_sort_order(self, text):
        self.sort_order = {
            "DB ID (Ascending)": "id ASC",
            "DB ID (Descending)": "id DESC",
            "Alphabetical (A-Z)": "name ASC",
            "Watching Status": "status",
            "Rating (High-Low)": "rating",
        }.get(text, "id ASC")
        self.load_data()

    def load_data(self):
        self.table.blockSignals(True)
        rows = self.repository.list_all(self.sort_order)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [row["id"], row["name"], row["seasons"], row["watched_episodes"], row["total_episodes"] if row["total_episodes"] is not None else "?", row["status"], row["rating"]]
            anilist_managed = row["anilist_id"] is not None
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                if col == 0:
                    item.setData(Qt.UserRole, row["anilist_id"] if anilist_managed else None)
                if col == 0 or anilist_managed:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if anilist_managed:
                    item.setToolTip("AniList-managed record — edit this data in AniList, then sync.")
                else:
                    item.setToolTip("Local record — editable in SyncCore.")
                self.table.setItem(row_index, col, item)
        self.table.blockSignals(False)

    def save_cell_change(self, row: int, column: int):
        if column not in {1, 2, 3, 5, 6}:
            return
        item = self.table.item(row, column)
        id_item = self.table.item(row, 0)
        if not item or not id_item:
            return
        db_id = int(id_item.text())
        if id_item.data(Qt.UserRole) is not None:
            QMessageBox.information(
                self,
                "AniList-Managed Record",
                "This record is managed by AniList. Edit it in AniList and sync SyncCore to update the local copy.",
            )
            self.load_data()
            return
        field_map = {1: ("name", str), 2: ("seasons", int), 3: ("watched_episodes", int), 5: ("status", str), 6: ("rating", float)}
        field, converter = field_map[column]
        try:
            value = converter(item.text())
            if field == "rating" and not 0 <= value <= 10:
                raise ValueError("Rating must be between 0 and 10.")
            if field == "seasons" and value < 1:
                raise ValueError("Seasons must be at least 1.")
            if field == "watched_episodes" and value < 0:
                raise ValueError("Watched episodes cannot be negative.")
            if field == "status" and value not in STATUSES:
                raise ValueError("Invalid status.")
            self.repository.update_field(db_id, field, value)
            if field in {"name", "status", "rating"}:
                self.load_data()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid Update", str(exc))
            self.load_data()

    def add_anime(self):
        dialog = AddAnimeDialog(self.client, self)
        dialog.data_ready.connect(self.insert_anime)
        dialog.exec()

    def insert_anime(self, data: dict):
        try:
            self.repository.upsert(Anime(**data))
            self.load_data()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", str(exc))

    def delete_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if not item:
            return
        answer = QMessageBox.question(
            self,
            "Delete Anime",
            "Delete the selected anime from the local database?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.repository.delete(int(item.text()))
            self.load_data()
            self.status_bar.showMessage("Anime deleted from the local database")
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", str(exc))

    def search_anime(self, text: str):
        query = text.lower().strip()
        for row in range(self.table.rowCount()):
            visible = any(query in (self.table.item(row, col).text().lower() if self.table.item(row, col) else "") for col in range(self.table.columnCount()))
            self.table.setRowHidden(row, not visible)

    def reconnect_anilist(self):
        if self.thread is not None and self.thread.isRunning():
            return
        try:
            self.reconnect_button.setEnabled(False)
            self.auth.reauthorize()
            self.status_bar.showMessage("AniList reconnected successfully")
            self.fetch_anilist_data()
        except Exception as exc:
            self.status_bar.showMessage("AniList reconnect failed")
            QMessageBox.critical(self, "AniList Authentication", str(exc))
        finally:
            self.reconnect_button.setEnabled(True)

    def fetch_anilist_data(self):
        if self.thread is not None and self.thread.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.setWindowTitle("SyncCore — Synchronizing...")
        self.status_bar.showMessage("Synchronizing with AniList...")
        self.thread = QThread()
        self.worker = SyncWorker(self.sync_service)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.data_synced.connect(self.sync_finished)
        self.worker.error_occurred.connect(self.sync_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._sync_thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _sync_thread_finished(self):
        # Clear Python references before the QThread C++ object is deleted so
        # a later Refresh click cannot call methods on a deleted QObject.
        self.thread = None
        self.worker = None

    def sync_finished(self, result: SyncResult):
        self.refresh_button.setEnabled(True)
        self.load_data()
        self.setWindowTitle("SyncCore — Data Synchronization")
        self.status_bar.showMessage(
            f"Sync completed • {result.fetched} upstream records • "
            f"{result.removed} stale records removed"
        )

    def sync_failed(self, message: str):
        self.refresh_button.setEnabled(True)
        self.setWindowTitle("SyncCore — Data Synchronization")
        self.status_bar.showMessage("Sync failed — local data was not partially committed")
        QMessageBox.critical(self, "Sync Error", message)

    def closeEvent(self, event):  # noqa: N802
        if self.thread and self.thread.isRunning():
            QMessageBox.information(
                self,
                "Synchronization in Progress",
                "Please wait for the current synchronization to finish before closing SyncCore.",
            )
            event.ignore()
            return
        self.repository.close()
        event.accept()
