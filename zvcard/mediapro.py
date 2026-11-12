"""Edit PRIVATE/M4ROOT/MEDIAPRO.XML (the camera's plain-text clip catalog).

When files are removed from the card outside the camera, the catalog keeps
listing them. That stale catalog (plus leftover thumbnails) is what makes the
camera's "Recover Image DB" fail. This module removes the <Material> blocks of
given clips, and the whole <Contents> block when no clip is left (which is
exactly how a freshly formatted card looks).
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

# A <Material ...> ... </Material> block (not self-closing), with its indentation/newline.
_MATERIAL = re.compile(
    r"[ \t]*<Material\b(?P<attrs>[^>]*?)(?<!/)>.*?</Material>[ \t]*(?:\r?\n)?", re.S
)
_URI = re.compile(r'\buri="([^"]*)"')
_EMPTY_CONTENTS = re.compile(
    r"[ \t]*<Contents\s*>\s*</Contents>[ \t]*(?:\r?\n)?|[ \t]*<Contents\s*/>[ \t]*(?:\r?\n)?"
)
_CLIP_URI = re.compile(r"(C\d{4})\.MP4$", re.I)
_SYSTEM_KIND = re.compile(r'<System\b[^>]*\bsystemKind="([^"]*)"')


def clip_id_from_uri(uri: str) -> str | None:
    base = uri.replace("\\", "/").rsplit("/", 1)[-1]
    m = _CLIP_URI.match(base)
    return m.group(1).upper() if m else None


def read_text(path: Path) -> str:
    return Path(path).read_bytes().decode("utf-8-sig", errors="replace")


def system_kind(text: str) -> str | None:
    m = _SYSTEM_KIND.search(text)
    return m.group(1) if m else None


def list_clip_ids(text: str) -> list[str]:
    ids = []
    for m in _MATERIAL.finditer(text):
        u = _URI.search(m.group("attrs"))
        cid = clip_id_from_uri(u.group(1)) if u else None
        if cid:
            ids.append(cid)
    return ids


def remove_materials(text: str, clip_ids: set[str]) -> tuple[str, list[str]]:
    """Return (new_text, removed_ids). Raises ValueError if the result is not valid XML."""
    wanted = {c.upper() for c in clip_ids}
    removed: list[str] = []

    def repl(m: re.Match) -> str:
        u = _URI.search(m.group("attrs"))
        cid = clip_id_from_uri(u.group(1)) if u else None
        if cid and cid in wanted:
            removed.append(cid)
            return ""
        return m.group(0)

    new = _MATERIAL.sub(repl, text)
    if removed and "<Material" not in new:
        new = _EMPTY_CONTENTS.sub("", new)
    if removed:
        try:
            ET.fromstring(new.encode("utf-8"))
        except ET.ParseError as e:  # never write something the camera can't parse
            raise ValueError(f"MEDIAPRO.XML would become invalid: {e}") from e
    return new, removed


def write_text(path: Path, text: str, had_bom: bool = False) -> None:
    path = Path(path)
    data = text.encode("utf-8")
    if had_bom:
        data = b"\xef\xbb\xbf" + data
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def remove_clips_from_file(path: Path, clip_ids: set[str]) -> list[str]:
    path = Path(path)
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig", errors="replace")
    new, removed = remove_materials(text, clip_ids)
    if removed:
        write_text(path, new, had_bom)
    return removed
