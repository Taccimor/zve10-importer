"""Modal dialogs: pick a LUT, choose import options."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import luts, ops
from .common import fmt_size


class LutDialog(QDialog):
    """Pick a LUT: recently used, found in editor folders, or browse. Files are never moved."""
    def __init__(self, parent, recents: list[str], extra_folders: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Apply LUT")
        self.setMinimumSize(560, 520)
        self.extra_folders = list(extra_folders)
        self.recents = [r for r in recents if Path(r).exists()]
        self.chosen: Path | None = None
        self.clear = False
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name...")
        self.search.textChanged.connect(self._fill)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._double)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        b_browse, b_folder = QPushButton("Browse for a .cube file..."), QPushButton("Add a folder to search...")
        b_none, b_apply, b_cancel = QPushButton("No LUT"), QPushButton("Apply"), QPushButton("Cancel")
        b_browse.clicked.connect(self._browse)
        b_folder.clicked.connect(self._add_folder)
        b_none.clicked.connect(self._none)
        b_apply.clicked.connect(self._apply)
        b_cancel.clicked.connect(self.reject)
        b_apply.setDefault(True)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        row1.addWidget(b_browse)
        row1.addWidget(b_folder)
        row2.addWidget(b_none)
        row2.addStretch(1)
        row2.addWidget(b_cancel)
        row2.addWidget(b_apply)
        lay = QVBoxLayout(self)
        lay.addWidget(self.search)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.note)
        lay.addLayout(row1)
        lay.addLayout(row2)
        self.found: list[tuple[str, Path]] = []
        self.rescan()

    def rescan(self):
        self.found = luts.find_luts(self.extra_folders)
        self._fill()

    def _fill(self, *_):
        needle = self.search.text().strip().lower()
        self.tree.clear()
        total = 0

        def leaf(parent, path: Path):
            it = QTreeWidgetItem([path.stem])
            it.setToolTip(0, str(path))
            it.setData(0, Qt.ItemDataRole.UserRole, str(path))
            parent.addChild(it)

        if self.recents:
            kids = [Path(r) for r in self.recents if needle in Path(r).stem.lower()]
            if kids:
                top = QTreeWidgetItem([f"Recently used  ({len(kids)})"])
                for p in kids:
                    leaf(top, p)
                total += len(kids)
                self.tree.addTopLevelItem(top)
                top.setExpanded(True)
        # one top-level node per searched folder, with its sub-folders mirrored below it
        roots: dict[tuple[str, str], list[Path]] = {}
        for label, root, p in self.found:
            if needle in p.stem.lower():
                roots.setdefault((label, str(root)), []).append(p)
        for (label, root_s), paths in roots.items():
            root = Path(root_s)
            top = QTreeWidgetItem([f"{label}  -  {root}  ({len(paths)})"])
            top.setToolTip(0, root_s)
            folders: dict[tuple, QTreeWidgetItem] = {(): top}
            for p in sorted(paths, key=lambda q: [part.lower() for part in q.relative_to(root).parts]):
                parts = p.relative_to(root).parts[:-1]
                for i in range(1, len(parts) + 1):
                    key = parts[:i]
                    if key not in folders:
                        node = QTreeWidgetItem([key[-1]])
                        folders[key[:-1]].addChild(node)
                        folders[key] = node
                leaf(folders[parts], p)
            total += len(paths)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        if needle:
            self.tree.expandAll()
        self.note.setText(f"{total} LUT(s) listed. Searched: DaVinci Resolve and Adobe folders"
                          + (f", plus {len(self.extra_folders)} of your folders" if self.extra_folders else "")
                          + ". LUT files are only read, never moved or changed.")

    def _selected_path(self) -> Path | None:
        items = self.tree.selectedItems()
        if items:
            p = items[0].data(0, Qt.ItemDataRole.UserRole)
            return Path(p) if p else None
        return None

    def _double(self, item, _col):
        p = item.data(0, Qt.ItemDataRole.UserRole)
        if p:
            self.chosen = Path(p)
            self.accept()

    def _apply(self):
        p = self._selected_path()
        if p is None:
            self.note.setText("Select a LUT in the list first (or use Browse).")
            return
        self.chosen = p
        self.accept()

    def _none(self):
        self.clear = True
        self.accept()

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose a LUT file", "", "LUT files (*.cube);;All files (*)")
        if f:
            self.chosen = Path(f)
            self.accept()

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with LUTs (searched recursively)")
        if d and d not in self.extra_folders:
            self.extra_folders.append(d)
            self.rescan()


class ImportDialog(QDialog):
    def __init__(self, parent, n_items: int, size: int, settings: QSettings, force_delete: bool = False,
                 discard_note: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Import")
        self.setMinimumWidth(520)
        self.force_delete = force_delete
        self.dest = QLineEdit(settings.value("dest", str(Path.home() / "Pictures" / "ZV-E10")))
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.dest, 1)
        rl.addWidget(browse)
        self.layout_box = QComboBox()
        for text, key in (("By date and type  (2026-11-10/Photos)", "date_type"),
                          ("By date  (2026-11-10)", "date"), ("All in one folder", "flat")):
            self.layout_box.addItem(text, key)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(settings.value("layout", "date_type"))))
        self.verify = QComboBox()
        for text, key in (("Compare contents (safest)", "hash"), ("Compare size only (faster)", "size"), ("No check", "none")):
            self.verify.addItem(text, key)
        self.verify.setCurrentIndex(max(0, self.verify.findData(settings.value("verify", "hash"))))
        self.delete_after = QCheckBox("Remove from the card after the copy is verified")
        if force_delete:
            self.delete_after.setChecked(True)
            self.delete_after.setEnabled(False)
        else:
            self.delete_after.setChecked(settings.value("delete_after", False, type=bool))
        form = QFormLayout()
        form.addRow("Destination:", row)
        form.addRow("Folders:", self.layout_box)
        form.addRow("Verify copy:", self.verify)
        text = f"{n_items} item(s), {fmt_size(size)} will be copied."
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(text))
        lay.addLayout(form)
        lay.addWidget(self.delete_after)
        self.ack = None
        if discard_note:
            warn = QLabel(discard_note)
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #ff8a65;")
            lay.addWidget(warn)
        if force_delete:
            self.ack = QCheckBox("I understand that the removed files cannot be recovered from the card")
            self.ack.toggled.connect(self._refresh)
            lay.addWidget(self.ack)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._refresh()

    def _refresh(self):
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(self.ack.isChecked() if self.ack else True)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Destination folder", self.dest.text())
        if d:
            self.dest.setText(d)

    def save(self, settings: QSettings):
        settings.setValue("dest", self.dest.text())
        settings.setValue("layout", self.layout_box.currentData())
        settings.setValue("verify", self.verify.currentData())
        if not self.force_delete:
            settings.setValue("delete_after", self.delete_after.isChecked())

    def options(self) -> ops.ImportOptions:
        return ops.ImportOptions(self.layout_box.currentData(), self.verify.currentData(),
                                 delete_after=self.delete_after.isChecked())
