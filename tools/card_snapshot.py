r"""Snapshot a camera memory card into a ZIP, for comparing what the camera changes between steps.

The card is only READ. The ZIP contains
  * every file up to --max-mb (default 5 MB) with the original folder structure: databases, XML sidecars,
    thumbnails, presets...  AND the whole AVF_INFO folder (the camera database), whatever its size;
  * MANIFEST.csv: EVERY file on the card (also big videos/RAWs, which are not copied): path, size,
    modified time, copied yes/no, SHA-256 of the copied ones;
  * INFO.txt: snapshot name, time, drive, card usage, errors.

Usage (interactive):      python tools/card_snapshot.py
Usage (non-interactive):  python tools/card_snapshot.py --root E:\ --name 2_shot
                          python tools/card_snapshot.py --root /Volumes/NO_NAME --name 2_shot --out ~/snapshots

See README.md ("Adapting to other Sony cameras") for the step-by-step protocol.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import shutil
import string
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path


def find_cards() -> list[Path]:
    """Windows drive letters that look like a camera card (never C:)."""
    found = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:\\")
            if letter != "C" and ((root / "DCIM").exists() or (root / "PRIVATE").exists()):
                found.append(root)
    return found


def ask_root() -> Path:
    cards = find_cards()
    if len(cards) == 1:
        if input(f"Card found: {cards[0]}  Use it? [Y/n] ").strip().lower() in ("", "y", "yes"):
            return cards[0]
    elif cards:
        print("Possible cards:", ", ".join(map(str, cards)))
    answer = input("Drive letter or folder of the card (e.g. E or /Volumes/NO_NAME): ").strip()
    if not answer:
        sys.exit("No card given.")
    return Path(f"{answer[0].upper()}:\\") if len(answer) == 1 and answer.isalpha() else Path(answer)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_file(src: Path, dst: Path) -> None:
    """Plain copy, with a chunked fallback for odd files."""
    try:
        shutil.copyfile(src, dst)
    except OSError:
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            for chunk in iter(lambda: fi.read(1 << 20), b""):
                fo.write(chunk)


def make_snapshot(root: Path, name: str, out_dir: Path, max_bytes: int) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="card_snap_"))
    data = tmp / "card"
    data.mkdir()
    manifest, errors, copied, skipped = [], [], 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        if "System Volume Information" in dirnames:
            dirnames.remove("System Volume Information")
        (data / rel_dir).mkdir(parents=True, exist_ok=True)          # keep empty folders: structure matters
        for fn in filenames:
            src, rel = Path(dirpath) / fn, rel_dir / fn
            try:
                st = src.stat()
                mtime = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
                if st.st_size <= max_bytes or rel.parts[0].upper() == "AVF_INFO":
                    dst = data / rel
                    copy_file(src, dst)
                    os.utime(dst, (st.st_atime, st.st_mtime))
                    manifest.append([rel.as_posix(), st.st_size, mtime, "yes", sha256_of(dst)])
                    copied += 1
                else:
                    manifest.append([rel.as_posix(), st.st_size, mtime, "no (too big)", ""])
                    skipped += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{rel}: {e}")
                manifest.append([rel.as_posix(), "", "", "ERROR", str(e)])
    manifest.sort(key=lambda r: r[0].lower())
    with open(tmp / "MANIFEST.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "size_bytes", "modified", "copied", "sha256"])
        w.writerows(manifest)
    try:
        u = shutil.disk_usage(root)
        usage = f"total={u.total} used={u.used} free={u.free}"
    except OSError:
        usage = "unknown"
    (tmp / "INFO.txt").write_text(
        f"Snapshot name: {name}\nCreated: {datetime.now().isoformat(timespec='seconds')}\nRoot: {root}\n"
        f"Card usage (bytes): {usage}\nFiles copied: {copied}\nBig files listed only: {skipped}\n"
        f"Errors: {len(errors)}\n" + "".join(f"  {e}\n" for e in errors), encoding="utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{name}.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dn, fn in os.walk(tmp):
            for f in fn:
                full = Path(dp) / f
                z.write(full, full.relative_to(tmp).as_posix())
            if not dn and not fn and Path(dp) != tmp:                # empty folder
                z.write(dp, Path(dp).relative_to(tmp).as_posix() + "/")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"Done: {copied} files copied, {skipped} big files listed, {len(errors)} errors -> {zip_path}")
    return zip_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Snapshot a camera card into a ZIP (read-only).")
    ap.add_argument("--root", help="card drive or folder (default: ask)")
    ap.add_argument("--name", help="snapshot name, e.g. 2_shot (default: ask)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent), help="folder for the ZIP")
    ap.add_argument("--max-mb", type=float, default=5, help="copy files up to this size (AVF_INFO always)")
    a = ap.parse_args()
    root = Path(a.root) if a.root else ask_root()
    if not root.is_dir():
        sys.exit(f"{root} not found.")
    name = a.name or input("Snapshot name (e.g. 1_clean, 2_shot, 3_ghosts): ").strip()
    name = "".join(c for c in name if c.isalnum() or c in "-_") or "snapshot"
    make_snapshot(root, name, Path(a.out), int(a.max_mb * 1024 * 1024))
    print("You can now remove the card.")


if __name__ == "__main__":
    main()
