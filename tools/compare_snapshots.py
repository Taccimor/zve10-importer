"""Compare two card snapshots made with tools/card_snapshot.py and print what changed.

    python tools/compare_snapshots.py 3_ghosts.zip 4_ghosts_deleted.zip [--diff-lines 40]

Reports, per folder: files added / removed / changed (size or checksum), a text diff of small XML/TXT
files (e.g. MEDIAPRO.XML), and for binary files (the AVF_INFO database) how many byte regions changed.
It also tells whether AVIN0001.INP and AVIN0001.BNP are identical mirror copies in each snapshot.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import io
import zipfile
from collections import defaultdict


def load(zpath: str):
    z = zipfile.ZipFile(zpath)
    rows = {}
    with z.open("MANIFEST.csv") as f:
        for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
            rows[r["path"].replace("\\", "/")] = r          # older snapshots used backslashes
    return z, rows


def read(z: zipfile.ZipFile, rel: str) -> bytes | None:
    try:
        return z.read("card/" + rel.replace("\\", "/"))
    except KeyError:
        return None


def regions(a: bytes, b: bytes, gap: int = 16):
    """Contiguous regions where two byte strings differ (merging differences closer than `gap`)."""
    n, out, i = min(len(a), len(b)), [], 0
    while i < n:
        if a[i] != b[i]:
            start = last = i
            j = i
            while j < n and j - last <= gap:
                if a[j] != b[j]:
                    last = j
                j += 1
            out.append((start, last + 1))
            i = last + 1
        else:
            i += 1
    if len(a) != len(b):
        out.append((n, max(len(a), len(b))))
    return out


def changed(ra: dict, rb: dict) -> bool:
    if ra["size_bytes"] != rb["size_bytes"]:
        return True
    if ra["sha256"] and rb["sha256"]:
        return ra["sha256"] != rb["sha256"]
    return ra["modified"] != rb["modified"]                      # big files: only size + time are known


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before"); ap.add_argument("after")
    ap.add_argument("--diff-lines", type=int, default=40, help="max lines of text diff per file")
    a = ap.parse_args()
    za, ma = load(a.before)
    zb, mb = load(a.after)
    added = sorted(set(mb) - set(ma))
    removed = sorted(set(ma) - set(mb))
    both = sorted(set(ma) & set(mb))
    diff = [p for p in both if changed(ma[p], mb[p])]
    print(f"BEFORE: {a.before}  ({len(ma)} files)\nAFTER : {a.after}  ({len(mb)} files)\n")
    for title, paths in (("ADDED", added), ("REMOVED", removed)):
        print(f"== {title} ({len(paths)})")
        for p in paths:
            r = (mb if title == "ADDED" else ma)[p]
            print(f"   {p}  ({r['size_bytes']} bytes)")
    print(f"\n== CHANGED ({len(diff)})")
    by_dir = defaultdict(int)
    for p in diff:
        by_dir[p.rsplit('/', 1)[0] if '/' in p else '.'] += 1
        ra, rb = ma[p], mb[p]
        print(f"   {p}  {ra['size_bytes']} -> {rb['size_bytes']} bytes")
        x, y = read(za, p), read(zb, p)
        if x is None or y is None:
            continue
        if p.lower().endswith((".xml", ".txt", ".csv")) and len(x) < 100_000:
            d = list(difflib.unified_diff(x.decode("utf-8", "replace").splitlines(),
                                          y.decode("utf-8", "replace").splitlines(), "before", "after", lineterm="", n=1))
            for line in d[:a.diff_lines]:
                print("        " + line)
            if len(d) > a.diff_lines:
                print(f"        ... ({len(d) - a.diff_lines} more diff lines)")
        else:
            rg = regions(x, y)
            total = sum(e - s for s, e in rg)
            print(f"        binary: {len(rg)} changed regions, {total} bytes; first at "
                  + ", ".join(f"0x{s:x}(+{e - s})" for s, e in rg[:6]))
    print("\n== AVF_INFO mirror check (INP == BNP?)")
    for label, z in (("before", za), ("after", zb)):
        inp, bnp = read(z, "AVF_INFO/AVIN0001.INP"), read(z, "AVF_INFO/AVIN0001.BNP")
        if inp is None or bnp is None:
            print(f"   {label}: INP/BNP not in this snapshot (other camera model, or AVF_INFO removed)")
        else:
            print(f"   {label}: identical={inp == bnp}, non-zero bytes={sum(1 for v in inp if v)} of {len(inp)}")


if __name__ == "__main__":
    main()
