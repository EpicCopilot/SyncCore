from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .sync_service import SyncResult, SyncService


class SyncWorker(QObject):
    finished = Signal()
    data_synced = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, service: SyncService):
        super().__init__()
        self.service = service

    @Slot()
    def run(self) -> None:
        try:
            result = self.service.sync_user_list()
            self.data_synced.emit(result)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            self.finished.emit()
