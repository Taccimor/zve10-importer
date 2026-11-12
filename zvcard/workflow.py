"""High level 'session': import some items, discard others, then leave the card clean.

End states:
  reset_done      card is now empty and the camera database was removed; the camera will
                  build a fresh one by itself at the next power-on. Nothing else to do.
  reset_failed    card is empty but AVF_INFO could not be deleted: delete the folder by hand in
                  Explorer (a backup zip was made), then power on the camera.
  needs_recovery  some media remains on the card: run 'Recover Image DB' once in the camera
                  (Setup > Page 4 > Recover Image DB) so it forgets the removed items.
  nothing_removed card untouched.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import ops
from .history import History
from .scan import Item, scan_card


# Text shown to the user for each end state (shared by the command line and the window).
STATE_MESSAGES = {
    "reset_done": "Card is empty and its database was reset. The camera will create a fresh one "
                  "at the next power-on. Nothing else to do.",
    "reset_failed": "The files were removed, but the AVF_INFO folder could not be deleted. Delete "
                    "the folder AVF_INFO on the card by hand in Explorer, then power on the camera "
                    "(a backup zip was saved in the backups folder).",
    "needs_recovery": "Some media remains on the card. On the camera run: Setup > Page 4 > "
                      "Recover Image DB (once), so it forgets the removed items.",
    "nothing_removed": "Nothing was removed from the card.",
    "dry_run": "Dry run: nothing was changed.",
}


@dataclass
class SessionReport:
    imported: list[ops.ItemResult] = field(default_factory=list)
    delete: ops.DeleteReport | None = None
    backup: Path | None = None
    state: str = "nothing_removed"
    messages: list[str] = field(default_factory=list)


def run_session(root: Path, import_items: list[Item], discard_items: list[Item],
                dest_root: Path | None, options: ops.ImportOptions, backup_dir: Path,
                reset_if_empty: bool = True, history: History | None = None,
                progress: ops.Progress = ops.no_progress, cancel: threading.Event | None = None,
                dry_run: bool = False) -> SessionReport:
    rep = SessionReport()
    to_delete: list[Item] = list(discard_items)

    if import_items:
        if dest_root is None:
            raise ValueError("dest_root is required when importing")
        rep.imported = ops.import_items(import_items, dest_root, options, progress, cancel, dry_run)
        for r in rep.imported:
            if r.status in ("failed", "cancelled"):
                rep.messages.append(f"{r.item.name}: {r.status} {r.message}".strip())
            elif history and not dry_run:
                history.mark(r.item, str(dest_root))
            if options.delete_after and r.safe_to_delete:
                to_delete.append(r.item)
        if history and not dry_run:
            history.save()

    if not to_delete:
        return rep

    rep.delete = ops.delete_items(root, to_delete, dry_run, progress)
    if dry_run:
        rep.state = "dry_run"
        return rep
    for it in rep.delete.skipped_protected:
        rep.messages.append(f"{it.name}: protected (read-only), left on the card.")
    for p, err in rep.delete.failed:
        rep.messages.append(f"could not delete {p}: {err}")

    info = scan_card(root)
    if not info.items and not info.has_leftovers and rep.delete.deleted_items:
        if reset_if_empty and not ops.reset_blockers(info):
            try:
                rep.backup = ops.reset_card(root, backup_dir)
                rep.state = "reset_done"
            except OSError as e:
                rep.state = "reset_failed"
                rep.messages.append(f"Files were removed, but the camera database folder "
                                    f"(AVF_INFO) could not be deleted: {e}")
        else:
            rep.state = "needs_recovery"
            rep.messages.append("Card is empty but could not be reset: " +
                                "; ".join(ops.reset_blockers(info)))
    elif rep.delete.deleted_items:
        rep.state = "needs_recovery"
    return rep
