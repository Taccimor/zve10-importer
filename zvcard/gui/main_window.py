"""The main window: card selection, thumbnail grid (with checkboxes), actions."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, QThreadPool, QTimer, Qt, Slot
from PySide6.QtGui import (
    QAction, QColor, QIcon, QKeySequence, QPixmap, QStandardItem, QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressDialog, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from .. import luts, media, ops, scan, workflow
from ..history import History
from .common import THUMB, Task, backup_dir, fmt_size, pil_to_qimage
from .dialogs import ImportDialog, LutDialog
from .preview import FullscreenWindow, PreviewPane
from .session import SessionRunner
from .widgets import CHECK_ROLE, ThumbDelegate, ThumbGrid


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZV-E10 Card Tool")
        self.resize(1250, 780)
        self.settings = QSettings("ZVCardTool", "ZVCardTool")
        self.history = History()
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(4)
        self.info: scan.CardInfo | None = None
        self.items: list[scan.Item] = []
        self._tasks: set[Task] = set()
        self._generation = 0
        self._preview_token = 0
        self._busy = False
        self._cancel = threading.Event()

        # --- top bar
        self.card_box = QComboBox()
        self.card_box.setMinimumWidth(220)
        self.card_box.activated.connect(self._card_chosen)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload)
        self.filter_box = QComboBox()
        for text, key in (("All", "all"), ("Photos", "photo"), ("Videos", "video"), ("Not imported yet", "new")):
            self.filter_box.addItem(text, key)
        self.filter_box.currentIndexChanged.connect(self.apply_filter)
        b_all, b_none, b_new = QPushButton("Select all"), QPushButton("Select none"), QPushButton("Select new")
        b_all.setToolTip("Check every item shown (Ctrl+A)")
        b_none.setToolTip("Uncheck everything")
        b_new.setToolTip("Check only the items not imported yet")
        b_all.clicked.connect(self.check_all)
        b_none.clicked.connect(self.clear_checks)
        b_new.clicked.connect(self.select_new)
        top = QHBoxLayout()
        for w in (QLabel("Card:"), self.card_box, refresh, QLabel("   Show:"), self.filter_box, b_all, b_none, b_new):
            top.addWidget(w)
        top.addStretch(1)

        # --- grid
        self.model = QStandardItemModel()
        self.view = ThumbGrid()
        self.view.setModel(self.model)
        self.view.setItemDelegate(ThumbDelegate(self.view))
        self.view.setViewMode(ThumbGrid.ViewMode.IconMode)
        self.view.setResizeMode(ThumbGrid.ResizeMode.Adjust)
        self.view.setMovement(ThumbGrid.Movement.Static)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)   # highlight = item being previewed
        self.view.setIconSize(QSize(THUMB, int(THUMB * 0.68)))
        self.view.setGridSize(QSize(THUMB + 20, int(THUMB * 0.68) + 52))
        self.view.setSpacing(4)
        self.view.setUniformItemSizes(True)
        self.view.setWordWrap(True)
        self.view.selectionModel().currentChanged.connect(self._current_changed)
        self.view.item_clicked.connect(self._grid_clicked)
        self._anchor: int | None = None          # last clicked row (start of a Shift+click range)
        self.placeholder = QIcon(self._placeholder_pixmap())

        # --- preview
        self.fs: FullscreenWindow | None = None
        self.lut = None
        self.preview = PreviewPane(self.settings.value("conform", False, type=bool))
        self.preview.fullscreen_requested.connect(self.toggle_fullscreen)
        self.preview.lut_button_clicked.connect(self.choose_lut)
        self.preview.conform_toggled.connect(lambda v: self.settings.setValue("conform", v))
        self.preview.lut_state_changed.connect(self._lut_state_changed)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._load_preview)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.view)
        split.addWidget(self.preview)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        # --- bottom bar
        self.sel_label = QLabel("")
        self.b_import = QPushButton("Import selected...")
        self.b_delete = QPushButton("Delete selected...")
        self.b_offload = QPushButton("Offload card...")
        self.b_clean = QPushButton("Clean leftovers")
        self.b_import.clicked.connect(self.do_import)
        self.b_delete.clicked.connect(self.do_delete)
        self.b_offload.clicked.connect(self.do_offload)
        self.b_clean.clicked.connect(self.do_clean)
        self.b_offload.setToolTip("Import the SELECTED items, permanently delete all the others, "
                                  "empty the card and reset the camera database.")
        self.b_clean.setToolTip("Remove thumbnails and catalog entries left behind after cutting files in Explorer.")
        bottom = QHBoxLayout()
        bottom.addWidget(self.sel_label, 1)
        for b in (self.b_clean, self.b_delete, self.b_import, self.b_offload):
            bottom.addWidget(b)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Connect the camera (USB, mass storage) or insert the card.")

        # shortcuts
        space = QAction(self)
        space.setShortcut(QKeySequence(Qt.Key.Key_Space))
        space.triggered.connect(self.preview.toggle_play)
        self.addAction(space)
        sel_all = QAction(self)
        sel_all.setShortcut(QKeySequence.StandardKey.SelectAll)
        sel_all.triggered.connect(self.check_all)
        self.addAction(sel_all)
        for keys, fn in ((["L"], lambda: self._fullscreen_toggle("lut")),
                         (["S"], lambda: self._fullscreen_toggle("slow")),
                         (["Shift+Left"], lambda: self.preview.seek_by(-5000)),
                         (["Shift+Right"], lambda: self.preview.seek_by(5000))):
            act = QAction(self)
            act.setShortcuts([QKeySequence(k) for k in keys])
            act.triggered.connect(fn)
            self.addAction(act)
        full = QAction(self)
        full.setShortcuts([QKeySequence(Qt.Key.Key_F), QKeySequence("Ctrl+F")])
        full.triggered.connect(self.toggle_fullscreen)
        self.addAction(full)

        self.fill_card_box()
        self._restore_lut()
        self.reload()

    # ---------------------------------------------------------- plumbing
    @Slot(object, object, object, object)
    def _dispatch(self, task, callback, result, error):
        # runs in the GUI thread (this object lives there), so callbacks may touch widgets
        self._tasks.discard(task)
        callback(result, error)

    def run_task(self, fn, callback):
        t = Task(fn, callback)
        self._tasks.add(t)
        t.signals.done.connect(self._dispatch)
        self.pool.start(t)

    def _placeholder_pixmap(self) -> QPixmap:
        pm = QPixmap(THUMB, int(THUMB * 0.68))
        pm.fill(QColor(50, 50, 50))
        return pm

    def set_busy(self, busy: bool):
        self._busy = busy
        for b in (self.b_import, self.b_delete, self.b_offload, self.b_clean, self.card_box):
            b.setEnabled(not busy)

    # ---------------------------------------------------------- card handling
    def fill_card_box(self):
        self.card_box.clear()
        for p in scan.find_cards():
            self.card_box.addItem(f"{p}  (camera card)", str(p))
        saved = self.settings.value("last_folder", "")
        if saved and self.card_box.findData(saved) < 0 and Path(saved).is_dir():
            self.card_box.addItem(saved, saved)
        self.card_box.addItem("Choose a folder...", "__choose__")
        self.card_box.setCurrentIndex(0)

    def _card_chosen(self, _index):
        data = self.card_box.currentData()
        if data == "__choose__":
            d = QFileDialog.getExistingDirectory(self, "Card (or copy of a card) folder")
            if not d:
                self.card_box.setCurrentIndex(0)
                return
            self.settings.setValue("last_folder", d)
            self.fill_card_box()
            self.card_box.setCurrentIndex(self.card_box.findData(d))
        self.reload()

    def current_root(self) -> str | None:
        d = self.card_box.currentData()
        return d if d and d != "__choose__" else None

    def reload(self):
        self.preview.release()
        self._generation += 1
        self.model.clear()
        self.info = None
        self.items = []
        self._update_buttons()
        root = self.current_root()
        if root is None or not Path(root).exists():
            # e.g. the camera was unplugged: rebuild the list; fall back to "Choose a folder..."
            self.fill_card_box()
            root = self.current_root()
            if root is None or not Path(root).exists():
                self.statusBar().showMessage("No camera card connected. Connect the camera (USB) "
                                             "or choose a folder, then press Refresh.")
                return
        self.statusBar().showMessage(f"Reading {root} ...")
        self.set_busy(True)
        self.run_task(lambda: scan.scan_card(root), lambda info, err, r=root: self._scan_done(info, err, r))

    def _scan_done(self, info, err, root=None):
        self.set_busy(False)
        if err is not None:
            if root is not None and not Path(root).exists():      # unplugged while reading
                self.fill_card_box()
                self.statusBar().showMessage("The card was disconnected.")
                return
            self.statusBar().showMessage(str(err))
            QMessageBox.warning(self, "Cannot read the card", str(err))
            return
        self.info = info
        self.items = info.items
        self._anchor = None
        gen = self._generation
        for i, it in enumerate(self.items):
            mark = "▶ " if it.kind == "video" else ""
            done = "  ·  imported" if self.history.is_imported(it) else ""
            row = QStandardItem(f"{mark}{it.name}\n{it.taken:%Y-%m-%d %H:%M}  {fmt_size(it.size)}{done}")
            row.setEditable(False)
            row.setIcon(self.placeholder)
            row.setData(it, Qt.ItemDataRole.UserRole)
            row.setData(False, CHECK_ROLE)
            self.model.appendRow(row)
            self.run_task(lambda it=it: media.make_thumbnail(it, THUMB),
                          lambda img, e, i=i, gen=gen: self._thumb_done(gen, i, img))
        msg = f"{info.root}: {len(info.photos)} photos, {len(info.videos)} videos."
        if info.has_leftovers:
            msg += "  Leftovers from files cut in Explorer were found - use 'Clean leftovers'."
        self.statusBar().showMessage(msg)
        self.apply_filter()
        self._update_buttons()

    def _thumb_done(self, gen, row, img):
        if gen != self._generation or img is None or row >= self.model.rowCount():
            return
        self.model.item(row).setIcon(QIcon(QPixmap.fromImage(pil_to_qimage(img))))

    # ---------------------------------------------------------- selection
    def item_at(self, row: int) -> scan.Item:
        return self.model.item(row).data(Qt.ItemDataRole.UserRole)

    def apply_filter(self):
        key = self.filter_box.currentData()
        for r in range(self.model.rowCount()):
            it = self.item_at(r)
            show = (key == "all" or key == it.kind or (key == "new" and not self.history.is_imported(it)))
            self.view.setRowHidden(r, not show)
        self._update_buttons()

    # Selection = the CHECKED items. (The highlighted item is only the one being previewed.)
    def is_checked(self, row: int) -> bool:
        return bool(self.model.item(row).data(CHECK_ROLE))

    def set_checked(self, row: int, value: bool):
        self.model.item(row).setData(bool(value), CHECK_ROLE)

    def visible_rows(self) -> list[int]:
        return [r for r in range(self.model.rowCount()) if not self.view.isRowHidden(r)]

    def check_where(self, pred):
        """Check every visible item for which pred(item) is true (never unchecks anything)."""
        for r in self.visible_rows():
            if pred(self.item_at(r)):
                self.set_checked(r, True)
        self._update_buttons()

    def check_all(self):
        self.check_where(lambda it: True)

    def clear_checks(self):
        for r in range(self.model.rowCount()):
            self.set_checked(r, False)
        self._update_buttons()

    def select_new(self):
        """Only the items not imported yet end up checked."""
        for r in range(self.model.rowCount()):
            self.set_checked(r, False)
        self.check_where(lambda it: not self.history.is_imported(it))

    def _grid_clicked(self, row: int, mode: str):
        """Mouse click on a thumbnail (modes: see widgets.click_mode). The 'anchor' is the last clicked
        row: a Shift+click ticks everything between the anchor and the clicked row."""
        if mode in ("box", "toggle"):
            self.set_checked(row, not self.is_checked(row))
        elif mode == "range":
            start = self._anchor
            if start is None:                             # nothing clicked yet: start from the previewed item
                cur = self.view.currentIndex()
                start = cur.row() if cur.isValid() else row
            lo, hi = sorted((start, row))
            for r in self.visible_rows():
                if lo <= r <= hi:
                    self.set_checked(r, True)
        self._anchor = row                                # (a plain click only previews and sets the anchor)
        self._update_buttons()

    def selected_items(self) -> list[scan.Item]:
        return [self.item_at(r) for r in range(self.model.rowCount()) if self.is_checked(r)]

    def unselected_items(self) -> list[scan.Item]:
        return [self.item_at(r) for r in range(self.model.rowCount()) if not self.is_checked(r)]

    def _update_buttons(self):
        sel = self.selected_items() if self.model.rowCount() else []
        size = sum(i.size for i in sel)
        hidden = sum(1 for r in range(self.model.rowCount()) if self.is_checked(r) and self.view.isRowHidden(r))
        text = f"{len(sel)} selected ({fmt_size(size)}) of {len(self.items)} items" if self.items else ""
        if hidden:
            text += f"  -  {hidden} of them hidden by the filter"
        self.sel_label.setText(text)
        has = bool(sel) and not self._busy
        self.b_import.setEnabled(has)
        self.b_delete.setEnabled(has)
        self.b_offload.setEnabled(bool(self.items) and not self._busy)
        leftovers = bool(self.info and self.info.has_leftovers)
        self.b_clean.setVisible(leftovers)
        self.b_clean.setEnabled(leftovers and not self._busy)

    # ---------------------------------------------------------- preview
    def _current_changed(self, current, _previous):
        self._preview_timer.start(180)       # debounce while arrow-keying through the grid

    def _load_preview(self, quiet: bool = False):
        idx = self.view.currentIndex()
        if not idx.isValid() or self.view.isRowHidden(idx.row()):
            return
        it = self.item_at(idx.row())
        self._preview_token += 1
        token = self._preview_token
        if not quiet:
            self.preview.show_message("Loading...")
        max_side = 3840 if self.fs is not None else 1600
        lut = self.preview.lut_in_use()             # read here (GUI thread), used in the worker

        def work():
            rows = media.describe(it)
            if it.kind == "photo":
                img = media.load_photo(it, max_side)
                if lut is not None:
                    img = luts.apply_lut(img, lut)  # preview only; the file is never changed
                return img, media.histogram(img), rows, None
            th = media.make_thumbnail(it, 400)
            meta = media.clip_metadata(it)
            fps = media.fps_value(meta.get("format_fps") or meta.get("fps"))
            return th, (media.histogram(th) if th else None), rows, fps

        def done(res, err):
            if token != self._preview_token:
                return
            if err is not None:
                self.preview.show_message(f"Cannot display this file:\n{err}")
                return
            img, hist, rows, fps = res
            if it.kind == "photo":
                self.preview.show_photo(pil_to_qimage(img), hist, rows)
            else:
                clip = next(f for f in it.files if f.role == "clip")
                self.preview.show_video(clip.path, hist, rows, fps)
        self.run_task(work, done)

    # ---------------------------------------------------------- full screen
    def toggle_fullscreen(self):
        if self.fs is not None:
            self.fs.close()
            return
        idx = self.view.currentIndex()
        if not idx.isValid() or self.preview.stack.currentIndex() not in (1, 2):
            return
        stage = self.preview.take_stage()
        self.preview.set_chrome_visible(False)
        self.fs = FullscreenWindow(stage, self._fullscreen_closed, self.step_item, self.preview.toggle_play,
                                   self.preview.seek_by, self._fullscreen_toggle)
        self.fs.showFullScreen()
        self.fs.activateWindow()
        self.fs.setFocus()
        if self.item_at(idx.row()).kind == "photo":
            self._load_preview(quiet=True)          # sharper version for the big screen

    def _fullscreen_closed(self):
        self.preview.restore_stage()
        self.preview.set_chrome_visible(True)
        self.fs = None

    def _fullscreen_toggle(self, name: str):
        """Shared by the main-window hotkeys and the full-screen window (S / L)."""
        if name == "slow":
            if self.preview.stack.currentIndex() == 2 and media.conform_ratio(self.preview._fps) is not None:
                self.preview.conform_box.toggle()
        elif name == "lut" and self.lut is not None:
            self.preview.use_lut.toggle()

    def step_item(self, delta: int):
        """Previous / next visible item (used by Left/Right in full screen)."""
        cur = self.view.currentIndex().row()
        r = cur + delta
        while 0 <= r < self.model.rowCount():
            if not self.view.isRowHidden(r):
                idx = self.model.index(r, 0)
                self.view.setCurrentIndex(idx)            # highlight + preview; checks are untouched
                self.view.scrollTo(idx)
                return
            r += delta

    # ---------------------------------------------------------- LUT
    def _lut_state_changed(self):
        """LUT chosen / switched on or off: photos must be re-rendered (videos update themselves)."""
        idx = self.view.currentIndex()
        if (idx.isValid() and self.preview.stack.currentIndex() == 1
                and self.item_at(idx.row()).kind == "photo"):
            self._load_preview(quiet=True)

    def _json_list(self, key: str) -> list[str]:
        try:
            v = json.loads(self.settings.value(key, "[]"))
            return [x for x in v if isinstance(x, str)]
        except (ValueError, TypeError):
            return []

    def _restore_lut(self):
        path = self.settings.value("last_lut", "")
        if path and Path(path).is_file():
            try:
                self.lut = luts.parse_cube(path)
                self.preview.set_lut(self.lut, self.lut.name, str(self.lut.path))
            except luts.LutError:
                pass

    def choose_lut(self):
        recents, folders = self._json_list("recent_luts"), self._json_list("lut_folders")
        dlg = LutDialog(self, recents, folders)
        accepted = dlg.exec()
        self.settings.setValue("lut_folders", json.dumps(dlg.extra_folders))
        if not accepted:
            return
        if dlg.clear:
            self.lut = None
            self.settings.setValue("last_lut", "")
            self.preview.set_lut(None)
            return
        if dlg.chosen is None:
            return
        try:
            lut = luts.parse_cube(dlg.chosen)
        except luts.LutError as e:
            QMessageBox.warning(self, "Cannot use this LUT", str(e))
            return
        self.lut = lut
        path = str(dlg.chosen)
        recents = [path] + [r for r in recents if r != path]
        self.settings.setValue("recent_luts", json.dumps(recents[:12]))
        self.settings.setValue("last_lut", path)
        self.preview.set_lut(lut, lut.name, str(lut.path))

    # ---------------------------------------------------------- actions
    def _start_session(self, import_items, discard_items, dest, options, reset_if_empty):
        self.preview.release()                      # free every file handle on the card
        self._cancel = threading.Event()
        dlg = QProgressDialog("Working...", "Cancel", 0, 1000, self)
        dlg.setWindowTitle("Please wait")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        cancel = self._cancel
        dlg.canceled.connect(cancel.set)
        self.set_busy(True)
        dlg.show()
        history, root = self.history, Path(self.current_root())
        runner = SessionRunner(self, dlg)
        runner.start(lambda progress: workflow.run_session(
            root, import_items, discard_items, dest, options, backup_dir(),
            reset_if_empty=reset_if_empty, history=history, progress=progress, cancel=cancel))

    def _finish_session(self, rep, err):
        if err is not None:
            QMessageBox.critical(self, "Error", f"{type(err).__name__}: {err}")
        else:
            lines = []
            bad = [r for r in rep.imported if r.status in ("failed", "cancelled")]
            if rep.imported:
                lines.append(f"Imported: {len(rep.imported) - len(bad)} item(s), problems: {len(bad)}.")
            if rep.delete:
                lines.append(f"Removed from the card: {len(rep.delete.deleted_items)} item(s).")
            lines.append(workflow.STATE_MESSAGES.get(rep.state, rep.state))
            lines += [f"- {m}" for m in rep.messages]
            box = QMessageBox.warning if (bad or rep.state in ("reset_failed", "needs_recovery")) else QMessageBox.information
            box(self, "Done", "\n".join(lines))
        self.reload()

    def do_import(self):
        items = self.selected_items()
        if not items:
            return
        dlg = ImportDialog(self, len(items), sum(i.size for i in items), self.settings)
        if not dlg.exec():
            return
        dlg.save(self.settings)
        self._start_session(items, [], Path(dlg.dest.text()), dlg.options(), reset_if_empty=True)

    def do_delete(self):
        items = self.selected_items()
        if not items:
            return
        ans = QMessageBox.question(
            self, "Delete from the card",
            f"Permanently delete {len(items)} item(s) ({fmt_size(sum(i.size for i in items))}) from the card?\n\n"
            "They are NOT copied anywhere and cannot be recovered.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._start_session([], items, None, ops.ImportOptions(), reset_if_empty=True)

    def do_offload(self):
        keep = self.selected_items()
        discard = self.unselected_items()
        note = ""
        if discard:
            note = (f"{len(discard)} item(s) ({fmt_size(sum(i.size for i in discard))}) that are NOT selected "
                    "will be PERMANENTLY DELETED without being copied.")
        if not keep:
            QMessageBox.warning(self, "Nothing selected",
                                "No item is selected, so everything would be deleted without importing anything.\n"
                                "Select the items you want to keep, or use 'Delete selected'.")
            return
        dlg = ImportDialog(self, len(keep), sum(i.size for i in keep), self.settings, force_delete=True,
                           discard_note=note + "\nAfterwards the card is emptied and its camera database is reset.")
        dlg.setWindowTitle("Offload card")
        if not dlg.exec():
            return
        dlg.save(self.settings)
        opts = dlg.options()
        opts.delete_after = True
        self._start_session(keep, discard, Path(dlg.dest.text()), opts, reset_if_empty=True)

    def do_clean(self):
        if not self.info:
            return
        ans = QMessageBox.question(
            self, "Clean leftovers",
            "Remove thumbnails/sidecar files of clips that no longer exist, and their entries in MEDIAPRO.XML?\n\n"
            "Afterwards run 'Recover Image DB' once on the camera (Setup > Page 4).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.preview.release()
        root = Path(self.current_root())
        self.set_busy(True)

        def done(rep, err):
            self.set_busy(False)
            if err is not None:
                QMessageBox.critical(self, "Error", str(err))
            else:
                QMessageBox.information(self, "Done", "Leftovers removed.\n" + workflow.STATE_MESSAGES["needs_recovery"])
            self.reload()
        self.run_task(lambda: ops.clean_leftovers(root), done)

    def closeEvent(self, e):
        if self.fs is not None:
            self.fs.close()
        self.preview.release()
        self.preview.shutdown()
        super().closeEvent(e)
