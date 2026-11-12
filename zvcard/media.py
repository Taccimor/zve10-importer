"""Previews, thumbnails, metadata and histograms (no GUI toolkit needed, so it is testable).

* ARW previews are read straight from the RAW's embedded JPEG (instant, no RAW decoding).
* Only the bytes that are needed are read from the card (fast over the camera's USB cable).
* Video thumbnails/metadata come from the camera's own sidecars (THMBNL jpg, clip XML).
"""
from __future__ import annotations

import io
import os
import re
import struct
import xml.etree.ElementTree as ET
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
_ORIENT = {
    2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE, 8: Image.Transpose.ROTATE_90,
}


# ------------------------------------------------------------------ ARW (TIFF) reader
@dataclass
class RawInfo:
    preview: bytes | None = None
    thumb: bytes | None = None
    orientation: int = 1
    width: int | None = None
    height: int | None = None
    exif: dict = field(default_factory=dict)


class _Tiff:
    def __init__(self, f):
        self.f = f
        head = self._at(0, 8)
        if head[:2] == b"II":
            self.e = "<"
        elif head[:2] == b"MM":
            self.e = ">"
        else:
            raise ValueError("not a TIFF/ARW file")
        self.first = struct.unpack(self.e + "I", head[4:8])[0]

    def _at(self, off, n):
        self.f.seek(off)
        return self.f.read(n)

    def ifd(self, off):
        """Return ({tag: (type, count, valueOrOffset, raw4)}, next_ifd_offset)."""
        e = self.e
        raw = self._at(off, 2)
        if len(raw) < 2:
            return {}, 0
        n = struct.unpack(e + "H", raw)[0]
        if n > 500:
            return {}, 0
        body = self._at(off + 2, n * 12 + 4)
        out = {}
        for i in range(n):
            tag, typ, cnt, val = struct.unpack_from(e + "HHII", body, i * 12)
            out[tag] = (typ, cnt, val, body[i * 12 + 8:i * 12 + 12])
        nxt = struct.unpack_from(e + "I", body, n * 12)[0] if len(body) >= n * 12 + 4 else 0
        return out, nxt

    def scalar(self, ent):
        typ, cnt, val, raw = ent
        if typ == 3:
            return struct.unpack(self.e + "H", raw[:2])[0]
        return val

    def rational(self, ent):
        typ, cnt, val, raw = ent
        if typ not in (5, 10) or cnt < 1:
            return None
        a, b = struct.unpack(self.e + ("II" if typ == 5 else "ii"), self._at(val, 8))
        return a / b if b else None

    def text(self, ent):
        typ, cnt, val, raw = ent
        data = raw[:cnt] if cnt <= 4 else self._at(val, cnt)
        return data.split(b"\0")[0].decode("ascii", "replace")


def read_arw(path: Path | str) -> RawInfo:
    """Embedded preview/thumbnail/EXIF of an ARW. Cached, because one click on a photo needs the same
    ~500 KB header several times (grid thumbnail, preview, info panel) and USB reads are slow."""
    st = os.stat(path)
    return _read_arw_cached(str(path), st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=32)
def _read_arw_cached(path: str, mtime_ns: int, size: int) -> RawInfo:
    # mtime/size are part of the key: a changed file is never served from the cache
    info = RawInfo()
    with open(path, "rb") as f:
        t = _Tiff(f)
        ifd0, ifd1_off = t.ifd(t.first)
        if 0x0112 in ifd0:
            info.orientation = t.scalar(ifd0[0x0112]) or 1
        # embedded full-size preview JPEG (1616x1080 on the ZV-E10)
        if 0x0201 in ifd0 and 0x0202 in ifd0:
            off, ln = t.scalar(ifd0[0x0201]), t.scalar(ifd0[0x0202])
            data = t._at(off, ln)
            if data[:2] == b"\xff\xd8":
                info.preview = data
        # real sensor size lives in the SubIFD
        if 0x014A in ifd0:
            sub_off = ifd0[0x014A][2] if ifd0[0x014A][1] == 1 else struct.unpack(
                t.e + "I", t._at(ifd0[0x014A][2], 4))[0]
            sub, _ = t.ifd(sub_off)
            if 0x0100 in sub and 0x0101 in sub:
                info.width, info.height = t.scalar(sub[0x0100]), t.scalar(sub[0x0101])
        # small thumbnail in IFD1
        if ifd1_off:
            ifd1, _ = t.ifd(ifd1_off)
            if 0x0201 in ifd1 and 0x0202 in ifd1:
                data = t._at(t.scalar(ifd1[0x0201]), t.scalar(ifd1[0x0202]))
                if data[:2] == b"\xff\xd8":
                    info.thumb = data
        # exposure data (optional: a damaged file must still show its preview)
        try:
            if 0x8769 in ifd0:
                ex, _ = t.ifd(ifd0[0x8769][2])
                if 0x829A in ex: info.exif["shutter"] = t.rational(ex[0x829A])
                if 0x829D in ex: info.exif["aperture"] = t.rational(ex[0x829D])
                if 0x8827 in ex: info.exif["iso"] = t.scalar(ex[0x8827])
                if 0x920A in ex: info.exif["focal"] = t.rational(ex[0x920A])
                if 0x9003 in ex: info.exif["date"] = t.text(ex[0x9003])
        except (struct.error, OSError, ValueError):
            info.exif.clear()
        if info.preview is None and info.thumb is None:
            raise ValueError("no embedded preview found")
    return info


def _apply_orientation(img: Image.Image, orientation: int) -> Image.Image:
    op = _ORIENT.get(orientation)
    return img.transpose(op) if op else img


# --------------------------------------------------------------------- photos
def _photo_files(item):
    raw = next((f for f in item.files if f.role == "raw"), None)
    jpg = next((f for f in item.files if f.role == "photo"
                and f.path.suffix.upper() in (".JPG", ".JPEG")), None)
    return raw, jpg


def load_photo(item, max_side: int = 1600) -> Image.Image:
    """Return an RGB image for display. JPEG is preferred when present, else the RAW's preview."""
    raw, jpg = _photo_files(item)
    if jpg is not None:
        with Image.open(jpg.path) as im:
            im.draft("RGB", (max_side, max_side))   # fast reduced-size JPEG decode
            im.load()
            out = ImageOps.exif_transpose(im)       # honour the camera's rotation flag
            out.load()
    elif raw is not None:
        info = read_arw(raw.path)
        with Image.open(io.BytesIO(info.preview or info.thumb)) as im:
            im.load()
            out = _apply_orientation(im, info.orientation)
    else:
        raise ValueError("no displayable file in this item")
    out = out.convert("RGB")
    out.thumbnail((max_side, max_side))
    return out


def make_thumbnail(item, size: int = 256) -> Image.Image | None:
    """Small RGB thumbnail, or None if nothing displayable (UI shows a placeholder)."""
    try:
        if item.kind == "video":
            th = next((f for f in item.files if f.role == "thumb"), None)
            if th is None:
                return None
            with Image.open(th.path) as im:
                im.draft("RGB", (size * 2, size * 2))
                out = im.convert("RGB")
        else:
            raw, jpg = _photo_files(item)
            if raw is not None and jpg is None:
                info = read_arw(raw.path)
                # the 160x120 thumb is too blurry for a grid; the 1616x1080 preview decodes
                # fast with JPEG draft mode (scaled-down decoding)
                data = info.preview if (info.preview and size > 160) else (info.thumb or info.preview)
                with Image.open(io.BytesIO(data)) as im:
                    im.draft("RGB", (size * 2, size * 2))
                    out = _apply_orientation(im.convert("RGB"), info.orientation)
            else:
                out = load_photo(item, size * 2)
        out.thumbnail((size, size))
        return out
    except Exception:  # noqa: BLE001  (corrupt/unsupported file: placeholder instead of a crash)
        return None


def histogram(img: Image.Image, max_side: int = 400) -> dict[str, list[int]]:
    """256-bin histograms: luma, r, g, b."""
    im = img.convert("RGB")
    im.thumbnail((max_side, max_side))
    h = im.histogram()
    return {"r": h[0:256], "g": h[256:512], "b": h[512:768], "luma": im.convert("L").histogram()}


# --------------------------------------------------------------------- video
def clip_metadata(item) -> dict:
    """Read the camera's clip XML (duration, fps, size, gamma...). Empty dict if missing."""
    xml = next((f for f in item.files if f.role == "clip_xml"), None)
    if xml is None:
        return {}
    try:
        root = ET.fromstring(Path(xml.path).read_bytes())
    except (ET.ParseError, OSError):
        return {}
    def local(tag): return tag.rsplit("}", 1)[-1]
    meta: dict = {}
    for el in root.iter():
        t = local(el.tag)
        if t == "Duration":
            meta["frames"] = int(el.get("value", "0") or 0)
        elif t == "VideoFrame":
            meta["codec"] = el.get("videoCodec")
            meta["fps"] = el.get("captureFps")
            meta["format_fps"] = el.get("formatFps")
        elif t == "VideoLayout":
            meta["width"], meta["height"] = el.get("pixel"), el.get("numOfVerticalLine")
        elif t == "Item" and el.get("name") == "CaptureGammaEquation":
            meta["gamma"] = el.get("value")
        elif t == "Item" and el.get("name") == "CaptureColorPrimaries":
            meta["primaries"] = el.get("value")
        elif t == "CreationDate":
            meta["created"] = el.get("value")
    return meta


def fps_value(text: str | None) -> float | None:
    """'59.94p' -> 59.94, '25p' -> 25.0"""
    m = re.match(r"\s*(\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def conform_ratio(fps: float | None) -> float | None:
    """Playback-speed factor that turns a high-frame-rate clip into normal-speed slow motion.

    50/60 fps -> 0.5 (plays as 25/30), 100/120 fps -> 0.25 (plays as 25/30).
    Normal frame rates (24/25/30) need no conforming -> None."""
    if not fps:
        return None
    if fps >= 99:
        return 0.25
    if fps >= 49:
        return 0.5
    return None


def is_log_like(meta: dict) -> bool:
    """True for S-Log clips (they look flat and need a LUT to be judged)."""
    g = (meta.get("gamma") or "").lower()
    return "s-log" in g or "slog" in g


# ---------------------------------------------------------------- info panel text
def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000


def _fmt_shutter(s: float | None) -> str | None:
    if not s:
        return None
    return f"1/{round(1 / s)} s" if s < 0.4 else f"{s:g} s"


def describe(item) -> list[tuple[str, str]]:
    rows = [("Name", item.name), ("Date", f"{item.taken:%Y-%m-%d %H:%M:%S}"),
            ("Size", _fmt_size(item.size))]
    try:
        if item.kind == "photo":
            raw, jpg = _photo_files(item)
            rows.append(("Files", " + ".join(sorted(f.path.suffix.upper().lstrip(".") for f in item.files))))
            if raw is not None:
                ri = read_arw(raw.path)
                if ri.width: rows.append(("Resolution", f"{ri.width} x {ri.height}"))
                ex = ri.exif
                exp = [x for x in (_fmt_shutter(ex.get("shutter")),
                                   f"f/{ex['aperture']:.1f}" if ex.get("aperture") else None,
                                   f"ISO {ex['iso']}" if ex.get("iso") else None,
                                   f"{ex['focal']:.0f} mm" if ex.get("focal") else None) if x]
                if exp: rows.append(("Exposure", "  ".join(exp)))
        else:
            m = clip_metadata(item)
            if m.get("width"): rows.append(("Resolution", f"{m['width']} x {m['height']}"))
            if m.get("fps"): rows.append(("Frame rate", m["fps"]))
            if m.get("frames") and m.get("fps"):
                base = re.match(r"(\d+(?:\.\d+)?)", m["fps"])
                if base: rows.append(("Length", f"{m['frames'] / float(base.group(1)):.1f} s"))
            if m.get("codec"): rows.append(("Codec", m["codec"]))
            if m.get("gamma"): rows.append(("Gamma", f"{m['gamma']}  ({m.get('primaries', '?')})"))
    except Exception:  # noqa: BLE001
        pass
    return rows
