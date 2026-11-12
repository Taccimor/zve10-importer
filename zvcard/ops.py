"""Operations that change data: import (copy + verify), delete, clean leftovers, reset.

Design rules (this code can destroy data, so it is deliberately strict):
  * Nothing is removed from the card unless the copy was verified (when importing).
  * Read-only files (often "protected" shots) are never deleted; they are reported.
  * MEDIAPRO.XML is only touched for clips whose MP4 is really gone.
  * AVF_INFO (the camera database) is removed ONLY when the card holds no media at all.
  * PRIVATE/SONY/SETTING (your presets) is never modified.
  * Every function has dry_run=True support and returns a report.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import mediapro
from .scan import CardFile, CardInfo, Item, find_child, m4root, scan_card

Progress = Callable[[dict], None]
CHUNK = 4 * 1024 * 1024


class Cancelled(Exception):
    pass


def no_progress(_: dict) -> None:
    """Default progress callback: ignore events."""


def is_protected(path: Path) -> bool:
    """Read-only attribute = treat as protected."""
    try:
        return not (os.stat(path).st_mode & stat.S_IWRITE)
    except OSError:
        return False


# ----------------------------------------------------------------- backups
def make_backup(root: Path, backup_dir: Path, label: str = "backup") -> Path:
    """Zip AVF_INFO, MEDIAPRO.XML and the SETTING folder (presets) before risky changes."""
    root, backup_dir = Path(root), Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    out = backup_dir / f"{time.strftime('%Y%m%d-%H%M%S')}_{label}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        avf = find_child(root, "AVF_INFO")
        if avf:
            for dp, _, fn in os.walk(avf):
                for f in fn:
                    p = Path(dp) / f
                    z.write(p, p.relative_to(root).as_posix())
        m4 = m4root(root)
        mp = find_child(m4, "MEDIAPRO.XML") if m4 else None
        if mp:
            z.write(mp, mp.relative_to(root).as_posix())
        priv = find_child(root, "PRIVATE")
        sony = find_child(priv, "SONY") if priv else None
        setting = find_child(sony, "SETTING") if sony else None
        if setting:
            for dp, _, fn in os.walk(setting):
                for f in fn:
                    p = Path(dp) / f
                    z.write(p, p.relative_to(root).as_posix())
    return out


# ------------------------------------------------------------------ import
@dataclass
class ItemResult:
    item: Item
    status: str            # ok | skipped_identical | failed | cancelled
    copied: list[Path] = field(default_factory=list)
    message: str = ""

    @property
    def safe_to_delete(self) -> bool:
        return self.status in ("ok", "skipped_identical")


@dataclass
class ImportOptions:
    layout: str = "date_type"            # flat | date | date_type
    verify: str = "hash"                 # none | size | hash
    copy_roles: tuple = ("photo", "raw", "clip", "clip_xml")
    delete_after: bool = False


def dest_dir(dest_root: Path, item: Item, layout: str) -> Path:
    day = item.taken.strftime("%Y-%m-%d")
    sub = "Videos" if item.kind == "video" else "Photos"
    if layout == "flat":
        return Path(dest_root)
    if layout == "date":
        return Path(dest_root) / day
    if layout == "date_type":
        return Path(dest_root) / day / sub
    raise ValueError(f"unknown layout {layout!r}")


def _hash_file(path: Path, cancel: threading.Event | None = None) -> str:
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            if cancel and cancel.is_set():
                raise Cancelled()
            h.update(chunk)
    return h.hexdigest()


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and _hash_file(a) == _hash_file(b)
    except OSError:
        return False


def _choose_targets(files: list[CardFile], folder: Path) -> list[tuple[CardFile, Path, bool]]:
    """Pick non-colliding names for the whole item. Returns (file, target, already_identical)."""
    n = 0
    while True:
        plan, ok = [], True
        for f in files:
            stem, ext = os.path.splitext(f.path.name)
            name = f.path.name if n == 0 else f"{stem}_{n}{ext}"
            target = folder / name
            if target.exists():
                if _same_file(f.path, target):
                    plan.append((f, target, True))
                else:
                    ok = False
                    break
            else:
                plan.append((f, target, False))
        if ok:
            return plan
        n += 1


def _copy_verified(src: Path, dst: Path, verify: str, cancel: threading.Event | None,
                   progress: Progress, bytes_done: list[int]) -> None:
    tmp = dst.with_name(dst.name + ".part")
    h = hashlib.blake2b(digest_size=20)
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while chunk := fi.read(CHUNK):
                if cancel and cancel.is_set():
                    raise Cancelled()
                fo.write(chunk)
                h.update(chunk)
                bytes_done[0] += len(chunk)
                progress({"event": "bytes", "done": bytes_done[0]})
            fo.flush()
            os.fsync(fo.fileno())
        if verify in ("size", "hash") and tmp.stat().st_size != src.stat().st_size:
            raise IOError("size mismatch after copy")
        if verify == "hash" and _hash_file(tmp, cancel) != h.hexdigest():
            raise IOError("checksum mismatch after copy")
        os.replace(tmp, dst)
        shutil.copystat(src, dst, follow_symlinks=True)  # keep the original timestamps
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def import_items(items: list[Item], dest_root: Path, options: ImportOptions | None = None,
                 progress: Progress = no_progress, cancel: threading.Event | None = None,
                 dry_run: bool = False) -> list[ItemResult]:
    opt = options or ImportOptions()
    dest_root = Path(dest_root)
    total = sum(f.size for it in items for f in it.files if f.role in opt.copy_roles)
    probe = dest_root
    while not probe.exists() and probe.parent != probe:   # nearest existing parent folder
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if not dry_run and total > free:
        raise OSError(f"Not enough space: need {total} bytes, only {free} free.")
    results: list[ItemResult] = []
    done = [0]
    progress({"event": "start", "total": total, "items": len(items)})
    for idx, it in enumerate(items):
        if cancel and cancel.is_set():
            results.append(ItemResult(it, "cancelled"))
            continue
        files = [f for f in it.files if f.role in opt.copy_roles]
        folder = dest_dir(dest_root, it, opt.layout)
        progress({"event": "item", "index": idx, "name": it.name})
        try:
            if not dry_run:
                folder.mkdir(parents=True, exist_ok=True)
            plan = _choose_targets(files, folder)
            copied: list[Path] = []
            all_identical = True
            for f, target, identical in plan:
                if identical:
                    done[0] += f.size
                    continue
                all_identical = False
                if not dry_run:
                    _copy_verified(f.path, target, opt.verify, cancel, progress, done)
                copied.append(target)
            results.append(ItemResult(it, "skipped_identical" if all_identical else "ok", copied))
        except Cancelled:
            results.append(ItemResult(it, "cancelled"))
        except Exception as e:  # noqa: BLE001
            results.append(ItemResult(it, "failed", message=str(e)))
    progress({"event": "end"})
    return results


# ------------------------------------------------------------------ delete
@dataclass
class DeleteReport:
    deleted_items: list[Item] = field(default_factory=list)
    skipped_protected: list[Item] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    catalog_removed: list[str] = field(default_factory=list)
    dry_run: bool = False



def delete_items(root: Path, items: list[Item], dry_run: bool = False,
                 progress: Progress = no_progress) -> DeleteReport:
    """Delete items with ALL their sidecar files, then clean MEDIAPRO.XML."""
    root = Path(root)
    rep = DeleteReport(dry_run=dry_run)
    gone_clips: set[str] = set()
    for it in items:
        if any(is_protected(f.path) for f in it.files):
            rep.skipped_protected.append(it)
            continue
        ok = True
        for f in it.files:
            progress({"event": "delete", "path": str(f.path)})
            if dry_run:
                continue
            try:
                f.path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                ok = False
                rep.failed.append((f.path, str(e)))
        if ok:
            rep.deleted_items.append(it)
        if it.kind == "video":
            clip_gone = not any(f.role == "clip" and f.path.exists() for f in it.files)
            if clip_gone or dry_run:
                gone_clips.add(it.clip_id)
    if gone_clips and not dry_run:
        rep.catalog_removed = _clean_catalog(root, gone_clips)
    elif gone_clips:
        rep.catalog_removed = sorted(gone_clips)
    return rep


def _clean_catalog(root: Path, clip_ids: set[str]) -> list[str]:
    m4 = m4root(root)
    mp = find_child(m4, "MEDIAPRO.XML") if m4 else None
    if not mp:
        return []
    return mediapro.remove_clips_from_file(mp, clip_ids)


# ---------------------------------------------------- leftovers (Explorer cuts)
def clean_leftovers(root: Path, dry_run: bool = False) -> DeleteReport:
    """Remove thumbnails/XML/proxies whose clip is gone and stale MEDIAPRO.XML entries.
    This is what you do after cutting files in Explorer; afterwards run
    'Recover Image DB' on the camera once."""
    root = Path(root)
    info = scan_card(root)
    rep = DeleteReport(dry_run=dry_run)
    for f in info.orphans:
        if dry_run:
            continue
        try:
            f.path.unlink()
        except OSError as e:
            rep.failed.append((f.path, str(e)))
    if info.stale_clips:
        if dry_run:
            rep.catalog_removed = list(info.stale_clips)
        else:
            rep.catalog_removed = _clean_catalog(root, set(info.stale_clips))
    return rep


# -------------------------------------------------------------------- reset

def _clear_attributes(path: Path) -> None:
    """Remove read-only (and on Windows hidden/system) flags so the entry can be deleted."""
    try:
        os.chmod(path, stat.S_IRWXU if Path(path).is_dir() else stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)  # FILE_ATTRIBUTE_NORMAL
        except Exception:  # noqa: BLE001
            pass


def remove_tree(path: Path) -> None:
    """Delete a folder the way Explorer does: clear read-only/system flags first.
    The camera creates AVF_INFO with flags that make a plain rmtree fail (WinError 5)."""
    path = Path(path)
    for dp, dn, fn in os.walk(path, topdown=False):
        for name in fn + dn:
            _clear_attributes(Path(dp) / name)
    _clear_attributes(path)
    shutil.rmtree(path)
    # Windows may report success while another program (an open Explorer window, antivirus,
    # the search indexer) still holds the folder open: the folder then lingers "delete pending".
    for _ in range(10):
        if not path.exists():
            return
        time.sleep(0.5)
    raise OSError(
        f"{path} is empty but Windows still has it open (an Explorer window, antivirus or "
        f"indexer may be using it). Close any window showing the card, safely eject and "
        f"re-plug it, then delete the folder by hand if it is still there."
    )

class ResetBlocked(Exception):
    pass


def reset_blockers(info: CardInfo) -> list[str]:
    msgs = []
    if info.items:
        msgs.append(f"{len(info.items)} photos/videos are still on the card.")
    if info.orphans:
        msgs.append(f"{len(info.orphans)} leftover sidecar files (run clean first).")
    if info.stale_clips:
        msgs.append(f"{len(info.stale_clips)} stale entries in MEDIAPRO.XML (run clean first).")
    if info.unknown:
        msgs.append(f"{len(info.unknown)} unrecognised files in camera folders, e.g. {info.unknown[0]}")
    if info.foreign:
        msgs.append(f"{len(info.foreign)} media files outside the camera folders, e.g. {info.foreign[0]}")
    return msgs


def reset_card(root: Path, backup_dir: Path, dry_run: bool = False) -> Path | None:
    """Delete the camera database (AVF_INFO) of an EMPTY card; the camera then creates a
    fresh empty one at the next power-on (verified on a ZV-E10). Returns the backup path."""
    root = Path(root)
    info = scan_card(root)
    blockers = reset_blockers(info)
    if blockers:
        raise ResetBlocked("Card is not empty:\n  - " + "\n  - ".join(blockers))
    avf = find_child(root, "AVF_INFO")
    if avf is None:
        return None
    if dry_run:
        return None
    backup = make_backup(root, backup_dir, "before_reset")
    remove_tree(avf)
    return backup
