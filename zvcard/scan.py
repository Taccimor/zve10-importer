"""Find a ZV-E10 card and describe what is on it (photos, videos, leftovers)."""
from __future__ import annotations

import os
import re
import string
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import mediapro

PHOTO_EXT = {".ARW", ".JPG", ".JPEG", ".HIF", ".HEIF"}
RAW_EXT = {".ARW"}
CLIP_RE = re.compile(r"^(C\d{4})\.MP4$", re.I)
CLIP_XML_RE = re.compile(r"^(C\d{4})M\d{2}\.XML$", re.I)
THUMB_RE = re.compile(r"^(C\d{4})T\d{2}\.JPG$", re.I)
PROXY_RE = re.compile(r"^(C\d{4})S\d{2}\.MP4$", re.I)
SUPPORTED_MODELS = {"ZV-E10"}


class NotACard(Exception):
    pass


@dataclass(frozen=True)
class CardFile:
    path: Path
    size: int
    mtime: float
    role: str  # photo | raw | clip | clip_xml | thumb | proxy


@dataclass
class Item:
    kind: str  # "photo" or "video"
    key: str   # "100MSDCF/DSC02996" or "C1079"
    files: list[CardFile] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]

    @property
    def main(self) -> CardFile:
        order = ["clip", "raw", "photo"]
        for role in order:
            for f in self.files:
                if f.role == role:
                    return f
        return self.files[0]

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def taken(self) -> datetime:
        return datetime.fromtimestamp(self.main.mtime)

    @property
    def clip_id(self) -> str | None:
        return self.name.upper() if self.kind == "video" else None


@dataclass
class CardInfo:
    root: Path
    model: str | None
    items: list[Item]
    orphans: list[CardFile]      # sidecars whose clip is gone (typical Explorer-cut leftovers)
    stale_clips: list[str]       # clips still listed in MEDIAPRO.XML but missing from the card
    unknown: list[Path]          # files in camera folders we do not recognise
    foreign: list[Path]          # media-like files outside the folders the camera uses

    @property
    def photos(self) -> list[Item]:
        return [i for i in self.items if i.kind == "photo"]

    @property
    def videos(self) -> list[Item]:
        return [i for i in self.items if i.kind == "video"]

    @property
    def has_leftovers(self) -> bool:
        return bool(self.orphans or self.stale_clips)


def find_child(path: Path, name: str) -> Path | None:
    """Case-insensitive child lookup."""
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.upper() == name.upper():
                    return Path(e.path)
    except OSError:
        pass
    return None


def _files(path: Path | None):
    if path is None:
        return []
    try:
        with os.scandir(path) as it:
            return [e for e in it if e.is_file()]
    except OSError:
        return []


def _dirs(path: Path | None):
    if path is None:
        return []
    try:
        with os.scandir(path) as it:
            return [e for e in it if e.is_dir()]
    except OSError:
        return []


def _cf(entry: os.DirEntry, role: str) -> CardFile:
    st = entry.stat()
    return CardFile(Path(entry.path), st.st_size, st.st_mtime, role)


def m4root(root: Path) -> Path | None:
    priv = find_child(root, "PRIVATE")
    return find_child(priv, "M4ROOT") if priv else None


def looks_like_card(root: Path) -> bool:
    root = Path(root)
    return root.is_dir() and (find_child(root, "DCIM") is not None or m4root(root) is not None)


def find_cards() -> list[Path]:
    """Windows: drive letters that look like a camera card (never C:)."""
    found = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            if letter == "C":
                continue
            p = Path(f"{letter}:\\")
            if p.exists() and looks_like_card(p):
                found.append(p)
    return found


def scan_card(root: Path | str, require_supported: bool = True) -> CardInfo:
    root = Path(root)
    if not looks_like_card(root):
        raise NotACard(f"{root} does not look like a camera card (no DCIM / PRIVATE/M4ROOT).")

    m4 = m4root(root)
    mp_path = find_child(m4, "MEDIAPRO.XML") if m4 else None
    catalog_text = mediapro.read_text(mp_path) if mp_path else ""
    model = mediapro.system_kind(catalog_text) if catalog_text else None
    if require_supported and model not in SUPPORTED_MODELS:
        raise NotACard(
            f"Unsupported or unknown camera model {model!r}. "
            f"Supported: {', '.join(sorted(SUPPORTED_MODELS))}."
        )

    items: list[Item] = []
    unknown: list[Path] = []

    # ---- photos: DCIM/<folder>/<name>.(ARW|JPG|...) grouped by stem
    dcim = find_child(root, "DCIM")
    for d in _dirs(dcim):
        groups: dict[str, Item] = {}
        for e in _files(Path(d.path)):
            ext = os.path.splitext(e.name)[1].upper()
            if ext in PHOTO_EXT:
                stem = os.path.splitext(e.name)[0].upper()
                key = f"{d.name}/{stem}"
                it = groups.setdefault(key, Item("photo", key))
                it.files.append(_cf(e, "raw" if ext in RAW_EXT else "photo"))
            else:
                unknown.append(Path(e.path))
        items.extend(groups.values())
    for e in _files(dcim):  # loose files directly in DCIM
        unknown.append(Path(e.path))

    # ---- videos: CLIP/Cnnnn.MP4 + CnnnnMnn.XML, THMBNL/CnnnnTnn.JPG, SUB/CnnnnSnn.MP4
    clips: dict[str, Item] = {}
    sidecars: list[CardFile] = []
    if m4:
        for e in _files(find_child(m4, "CLIP")):
            if CLIP_RE.match(e.name):
                cid = e.name[:5].upper()
                clips.setdefault(cid, Item("video", cid)).files.append(_cf(e, "clip"))
            elif CLIP_XML_RE.match(e.name):
                sidecars.append(_cf(e, "clip_xml"))
            else:
                unknown.append(Path(e.path))
        for e in _files(find_child(m4, "THMBNL")):
            if THUMB_RE.match(e.name):
                sidecars.append(_cf(e, "thumb"))
            else:
                unknown.append(Path(e.path))
        for e in _files(find_child(m4, "SUB")):
            if PROXY_RE.match(e.name):
                sidecars.append(_cf(e, "proxy"))
            else:
                unknown.append(Path(e.path))
        for e in _files(find_child(m4, "GENERAL")):
            unknown.append(Path(e.path))
        for e in _dirs(m4):  # unexpected sub-folders of M4ROOT
            if e.name.upper() not in {"CLIP", "THMBNL", "SUB", "GENERAL"}:
                unknown.extend(Path(p.path) for p in _files(Path(e.path)))

    orphans: list[CardFile] = []
    for sc in sidecars:
        cid = sc.path.name[:5].upper()
        if cid in clips:
            clips[cid].files.append(sc)
        else:
            orphans.append(sc)
    items.extend(clips.values())

    stale = sorted(set(mediapro.list_clip_ids(catalog_text)) - set(clips))

    # ---- media-like files outside the camera's own folders (would be hidden by a DB reset)
    foreign: list[Path] = []
    known_top = {"AVF_INFO", "DCIM", "PRIVATE", "SYSTEM VOLUME INFORMATION", "$RECYCLE.BIN"}
    with os.scandir(root) as it:
        top = list(it)
    for e in top:
        if e.is_dir():
            if e.name.upper() in known_top:   # PRIVATE is checked separately below
                continue
            foreign.extend(_media_like(Path(e.path)))
        elif os.path.splitext(e.name)[1].upper() in PHOTO_EXT | {".MP4", ".MOV", ".MTS"}:
            foreign.append(Path(e.path))
    priv = find_child(root, "PRIVATE")
    for e in _dirs(priv):
        if e.name.upper() not in {"M4ROOT", "SONY"}:
            foreign.extend(_media_like(Path(e.path)))

    items.sort(key=lambda i: (i.main.mtime, i.key))
    return CardInfo(root, model, items, orphans, stale, unknown, foreign)


def _media_like(path: Path) -> list[Path]:
    out = []
    for dp, _, fn in os.walk(path):
        for f in fn:
            if os.path.splitext(f)[1].upper() in PHOTO_EXT | {".MP4", ".MOV", ".MTS", ".M2TS"}:
                out.append(Path(dp) / f)
    return out
