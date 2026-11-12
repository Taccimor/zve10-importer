r"""Command line front-end (the GUI will use the same engine).

Examples (Windows):
  python -m zvcard detect
  python -m zvcard scan E:\
  python -m zvcard offload E:\ D:\Camera --keep-on-card        (copy only)
  python -m zvcard offload E:\ D:\Camera                        (copy, verify, wipe, reset DB)
  python -m zvcard clean E:\                                    (after cutting files in Explorer)
  python -m zvcard delete E:\ DSC02996 C1079                    (discard specific items)
Add --dry-run to any command to see what would happen without changing anything.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import ops, scan, workflow
from .history import History



def _backup_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "ZVCardTool" / "backups"


def _mb(n: int) -> str:
    return f"{n / 1_000_000:,.1f} MB"


def _print_card(info: scan.CardInfo) -> None:
    print(f"Card {info.root}  model={info.model}")
    for it in info.items:
        roles = "+".join(sorted({f.role for f in it.files}))
        print(f"  {it.kind:5} {it.key:22} {it.taken:%Y-%m-%d %H:%M}  {_mb(it.size):>10}  [{roles}]")
    print(f"  {len(info.photos)} photos, {len(info.videos)} videos")
    if info.orphans:
        print(f"  LEFTOVERS: {len(info.orphans)} sidecar files without clip")
    if info.stale_clips:
        print(f"  LEFTOVERS: stale MEDIAPRO.XML entries: {', '.join(info.stale_clips)}")
    for label, lst in (("UNKNOWN", info.unknown), ("FOREIGN", info.foreign)):
        for p in lst[:5]:
            print(f"  {label}: {p}")


def _confirm(text: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return input(f"{text} Type YES to continue: ").strip() == "YES"


def _select(info: scan.CardInfo, names: list[str]) -> list[scan.Item]:
    if not names:
        return list(info.items)
    wanted = {n.upper() for n in names}
    return [i for i in info.items if i.name.upper() in wanted]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zvcard", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect")
    for name in ("scan", "clean", "reset"):
        p = sub.add_parser(name)
        p.add_argument("card")
        if name != "scan":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--yes", action="store_true")
    p = sub.add_parser("offload")
    p.add_argument("card"); p.add_argument("dest")
    p.add_argument("--only", nargs="*", default=[], help="item names (default: everything)")
    p.add_argument("--keep-on-card", action="store_true")
    p.add_argument("--layout", choices=["flat", "date", "date_type"], default="date_type")
    p.add_argument("--verify", choices=["none", "size", "hash"], default="hash")
    p.add_argument("--no-reset", action="store_true")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--yes", action="store_true")
    p = sub.add_parser("delete")
    p.add_argument("card"); p.add_argument("names", nargs="+")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "detect":
            cards = scan.find_cards()
            print("\n".join(map(str, cards)) or "No card found.")
            return 0
        if a.cmd == "scan":
            _print_card(scan.scan_card(a.card)); return 0
        if a.cmd == "clean":
            info = scan.scan_card(a.card); _print_card(info)
            if not info.has_leftovers:
                print("No leftovers."); return 0
            if not a.dry_run and not _confirm("Remove leftover files and stale catalog entries?", a.yes):
                return 1
            r = ops.clean_leftovers(Path(a.card), a.dry_run)
            print(f"Catalog entries removed: {r.catalog_removed}; failed: {r.failed}")
            print(workflow.STATE_MESSAGES["needs_recovery"]); return 0
        if a.cmd == "reset":
            if not a.dry_run and not _confirm("Delete the camera database of this EMPTY card?", a.yes):
                return 1
            b = ops.reset_card(Path(a.card), _backup_dir(), a.dry_run)
            print(f"Backup: {b}\n" + workflow.STATE_MESSAGES["reset_done"]); return 0
        if a.cmd == "delete":
            info = scan.scan_card(a.card); items = _select(info, a.names)
            print(f"{len(items)} items will be PERMANENTLY deleted: {[i.name for i in items]}")
            if not a.dry_run and not _confirm("Delete them?", a.yes):
                return 1
            rep = workflow.run_session(Path(a.card), [], items, None, ops.ImportOptions(),
                                       _backup_dir(), dry_run=a.dry_run)
            print(workflow.STATE_MESSAGES.get(rep.state, rep.state)); [print(" -", m) for m in rep.messages]; return 0
        if a.cmd == "offload":
            info = scan.scan_card(a.card); items = _select(info, a.only)
            print(f"{len(items)} items, {_mb(sum(i.size for i in items))} -> {a.dest}")
            if not a.keep_on_card:
                print("They will be REMOVED from the card after a verified copy.")
            if not a.dry_run and not _confirm("Proceed?", a.yes):
                return 1
            opt = ops.ImportOptions(a.layout, a.verify, delete_after=not a.keep_on_card)
            last = [-1]
            def prog(e):
                if e["event"] == "item" and e["index"] != last[0]:
                    last[0] = e["index"]; print(f"  [{e['index'] + 1}/{len(items)}] {e['name']}")
            rep = workflow.run_session(Path(a.card), items, [], Path(a.dest), opt, _backup_dir(),
                                       reset_if_empty=not a.no_reset, history=History(),
                                       progress=prog, dry_run=a.dry_run)
            bad = [r for r in rep.imported if r.status in ("failed", "cancelled")]
            verb = "Would import" if a.dry_run else "Imported OK"
            print(f"{verb}: {len(rep.imported) - len(bad)}, problems: {len(bad)}")
            print(workflow.STATE_MESSAGES.get(rep.state, rep.state)); [print(" -", m) for m in rep.messages]
            return 0 if not bad else 2
    except (scan.NotACard, ops.ResetBlocked, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0
