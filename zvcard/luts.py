"""Load and apply .cube LUTs (1D and 3D) and find them in the usual editor folders.

Used for *viewing only*: the LUT is applied to preview frames; the file is never changed or moved.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter

MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_FOUND = 3000


class LutError(Exception):
    pass


@dataclass
class Lut:
    path: Path
    title: str
    kind: str                 # "3d" or "1d"
    size: int
    table: list               # flat floats, r fastest (the .cube order)
    domain_min: tuple = (0.0, 0.0, 0.0)
    domain_max: tuple = (1.0, 1.0, 1.0)
    _filter: object = None

    @property
    def name(self) -> str:
        """The file name without extension (the .cube TITLE line is often not what users expect)."""
        return self.path.stem


def parse_cube(path: Path | str) -> Lut:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise LutError("LUT file is unreasonably large")
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as e:
        raise LutError(f"Cannot read {path}: {e}") from e
    title, size3, size1 = "", None, None
    dmin, dmax = [0.0] * 3, [1.0] * 3
    data_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split(None, 1)[0].upper()
        if head == "TITLE":
            title = line.split(None, 1)[1].strip().strip('"') if " " in line else ""
        elif head == "LUT_3D_SIZE":
            size3 = int(line.split()[1])
        elif head == "LUT_1D_SIZE":
            size1 = int(line.split()[1])
        elif head == "DOMAIN_MIN":
            dmin = [float(x) for x in line.split()[1:4]]
        elif head == "DOMAIN_MAX":
            dmax = [float(x) for x in line.split()[1:4]]
        elif head in ("LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE"):
            lo, hi = (float(x) for x in line.split()[1:3])
            dmin, dmax = [lo] * 3, [hi] * 3
        elif head[0] in "+-.0123456789":
            data_lines.append(line)
        # other keywords are ignored
    try:                                  # one bulk conversion is several times faster than line by line
        values = list(map(float, " ".join(data_lines).split()))
    except ValueError as e:
        raise LutError(f"LUT contains a non-numeric value: {e}") from e
    if size3:
        kind, size = "3d", size3
        if not 2 <= size <= 256:
            raise LutError(f"Unsupported 3D LUT size {size}")
    elif size1:
        kind, size = "1d", size1
        if size < 2:
            raise LutError("Unsupported 1D LUT size")
    else:
        raise LutError("Not a .cube LUT (no LUT_3D_SIZE / LUT_1D_SIZE)")
    expected = (size ** 3 if kind == "3d" else size) * 3
    if len(values) != expected:
        raise LutError(f"LUT has {len(values)} values, expected {expected}")
    return Lut(path, title, kind, size, values, tuple(dmin), tuple(dmax))


def _prepare(lut: Lut):
    """Build the fast Pillow filter / lookup table once."""
    if lut._filter is not None:
        return lut._filter
    if lut.kind == "3d":
        lut._filter = ImageFilter.Color3DLUT(lut.size, lut.table, channels=3)
    else:
        n = lut.size
        tables = []
        for ch in range(3):
            samples = lut.table[ch::3]
            lut_ch = []
            for i in range(256):
                x = i / 255 * (n - 1)
                i0 = min(int(x), n - 2)
                f = x - i0
                v = samples[i0] * (1 - f) + samples[i0 + 1] * f
                lut_ch.append(max(0, min(255, round(v * 255))))
            tables.append(lut_ch)
        lut._filter = tables[0] + tables[1] + tables[2]
    return lut._filter


def apply_lut(img: Image.Image, lut: Lut) -> Image.Image:
    """Return a new RGB image with the LUT applied."""
    rgb = img if img.mode == "RGB" else img.convert("RGB")
    f = _prepare(lut)
    return rgb.filter(f) if lut.kind == "3d" else rgb.point(f)


# ------------------------------------------------------------ finding LUTs
def candidate_folders(extra: list[str] | None = None) -> list[tuple[str, Path]]:
    """(source label, folder) pairs that exist on this computer."""
    env = os.environ
    pd, ad = env.get("PROGRAMDATA", ""), env.get("APPDATA", "")
    pf = [env.get("PROGRAMFILES", ""), env.get("PROGRAMFILES(X86)", "")]
    home = Path.home()
    spec: list[tuple[str, str]] = []
    if pd:
        spec += [("DaVinci Resolve", f"{pd}/Blackmagic Design/DaVinci Resolve/Support/LUT"),
                 ("Adobe", f"{pd}/Adobe/Common/LUTs")]
    if ad:
        spec += [("DaVinci Resolve", f"{ad}/Blackmagic Design/DaVinci Resolve/Support/LUT"),
                 ("Adobe", f"{ad}/Adobe/Common/LUTs")]
    for base in filter(None, pf):
        spec += [("Adobe Premiere Pro", g) for g in glob.glob(f"{base}/Adobe/Adobe Premiere Pro*/Lumetri/LUTs")]
        spec += [("Adobe After Effects", g) for g in glob.glob(f"{base}/Adobe/Adobe After Effects*/Support Files/Lumetri/LUTs")]
        spec += [("Adobe", f"{base}/Adobe/Common/LUTs"),
                 ("DaVinci Resolve", f"{base}/Blackmagic Design/DaVinci Resolve/LUT")]
    spec += [("Documents", str(home / "Documents" / "LUTs")), ("Documents", str(home / "Documents" / "LUT")),
             ("DaVinci Resolve", "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT")]
    out, seen = [], set()
    for label, folder in spec:
        p = Path(folder)
        if p.is_dir() and str(p).lower() not in seen:
            seen.add(str(p).lower())
            out.append((label, p))
    for folder in extra or []:
        p = Path(folder)
        if p.is_dir() and str(p).lower() not in seen:
            seen.add(str(p).lower())
            out.append(("Your folders", p))
    return out


def find_luts(extra: list[str] | None = None, max_depth: int = 6) -> list[tuple[str, Path, Path]]:
    """Search the known folders (plus yours) for .cube files. Never moves or changes anything.
    Returns (source label, root folder, file) so callers can keep the folder structure."""
    found: list[tuple[str, Path, Path]] = []
    seen: set[str] = set()
    for label, root in candidate_folders(extra):
        base_depth = len(root.parts)
        for dp, dn, fn in os.walk(root):
            if len(Path(dp).parts) - base_depth >= max_depth:
                dn[:] = []
            for f in sorted(fn):
                if f.lower().endswith(".cube"):
                    p = Path(dp) / f
                    key = str(p).lower()
                    if key not in seen:
                        seen.add(key)
                        found.append((label, root, p))
                        if len(found) >= MAX_FOUND:
                            return found
    return found
