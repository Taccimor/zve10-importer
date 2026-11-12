"""Runs an import/delete session in a background thread and reports back to the GUI thread."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Slot


class SessionRunner(QObject):
    """Runs workflow.run_session in a thread; progress/finish are handled in the GUI thread."""
    progress = Signal(object)
    finished = Signal(object, object)       # report, error

    def __init__(self, window, dialog):
        super().__init__(window)            # lives in the GUI thread
        self.window, self.dlg = window, dialog
        self.total = 1
        self.progress.connect(self.on_progress)
        self.finished.connect(self.on_finished)

    @Slot(object)
    def on_progress(self, e):
        if e["event"] == "start":
            self.total = max(1, e["total"])
        elif e["event"] == "bytes":
            self.dlg.setValue(min(1000, int(e["done"] * 1000 / self.total)))
        elif e["event"] == "item":
            self.dlg.setLabelText(f"Copying {e['name']} ...")
        elif e["event"] == "delete":
            self.dlg.setLabelText("Removing files from the card ...")

    @Slot(object, object)
    def on_finished(self, rep, err):
        self.dlg.close()
        self.window.set_busy(False)
        self.window._finish_session(rep, err)
        self.deleteLater()

    def start(self, fn):
        def runner():
            try:
                rep, err = fn(lambda e: self.progress.emit(e)), None
            except BaseException as ex:  # noqa: BLE001
                rep, err = None, ex
            self.finished.emit(rep, err)
        threading.Thread(target=runner, daemon=True).start()
