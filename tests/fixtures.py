"""Synthetic test data. Nothing here comes from a real camera (no serial numbers, no real photos):
everything is generated on the fly, so the repository stays small and free of personal data.

The structures mimic what was observed on a real ZV-E10 (see README, "Developer notes")."""
from __future__ import annotations

import io
import os
import struct
from functools import lru_cache
from pathlib import Path

from PIL import Image

# --------------------------------------------------------------------------- images
@lru_cache(maxsize=None)
def jpeg_bytes(size: tuple[int, int], quality: int = 70) -> bytes:
    """A small coloured gradient as JPEG bytes."""
    g = Image.linear_gradient("L").resize(size)
    img = Image.merge("RGB", (g, g.transpose(Image.Transpose.FLIP_TOP_BOTTOM), g))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# --------------------------------------------------------------------------- fake ARW
def make_fake_arw(path: Path, *, orientation: int = 1, preview: tuple[int, int] = (1616, 1080),
                  sensor: tuple[int, int] = (6048, 4024), iso: int = 800, shutter=(1, 200),
                  aperture=(8, 1), focal=(77, 1), date: str = "2026:01:02 03:04:05",
                  raw_padding: int = 0) -> dict:
    """Write a minimal ARW-like TIFF: IFD0 (orientation, preview JPEG, SubIFD, EXIF), a SubIFD with the
    sensor size, an EXIF IFD with exposure data and an IFD1 with a small thumbnail.
    Returns {"preview_end": byte offset where the preview JPEG ends}."""
    SHORT, LONG, ASCII, RATIONAL = 3, 4, 2, 5
    prev, thumb = jpeg_bytes(preview), jpeg_bytes((160, 120))
    size = lambda n: 2 + 12 * n + 4                       # entries + count + next-IFD pointer
    ifd0 = 8
    sub = ifd0 + size(5)
    exif = sub + size(2)
    ifd1 = exif + size(5)
    data_start = ifd1 + size(2)
    blobs = bytearray()

    def alloc(b: bytes) -> int:
        off = data_start + len(blobs)
        blobs.extend(b + (b"\0" if len(b) % 2 else b""))
        return off

    def entry(tag, typ, count, value) -> bytes:
        raw = struct.pack("<H", value) + b"\0\0" if typ == SHORT else struct.pack("<I", value)
        return struct.pack("<HHI", tag, typ, count) + raw

    def ifd(entries: list, nxt: int = 0) -> bytes:
        return struct.pack("<H", len(entries)) + b"".join(entries) + struct.pack("<I", nxt)

    rat = lambda ab: alloc(struct.pack("<II", *ab))
    shutter_off, aperture_off, focal_off = rat(shutter), rat(aperture), rat(focal)   # like a real ARW:
    date_off = alloc(date.encode() + b"\0")                                            # exposure data first,
    thumb_off = alloc(thumb)
    prev_off = alloc(prev)                                                             # preview last
    out = bytearray(b"II*\0" + struct.pack("<I", ifd0))
    out += ifd([entry(0x0112, SHORT, 1, orientation), entry(0x014A, LONG, 1, sub),
                entry(0x0201, LONG, 1, prev_off), entry(0x0202, LONG, 1, len(prev)),
                entry(0x8769, LONG, 1, exif)], nxt=ifd1)
    out += ifd([entry(0x0100, LONG, 1, sensor[0]), entry(0x0101, LONG, 1, sensor[1])])
    out += ifd([entry(0x829A, RATIONAL, 1, shutter_off), entry(0x829D, RATIONAL, 1, aperture_off),
                entry(0x8827, SHORT, 1, iso), entry(0x9003, ASCII, len(date) + 1, date_off),
                entry(0x920A, RATIONAL, 1, focal_off)])
    out += ifd([entry(0x0201, LONG, 1, thumb_off), entry(0x0202, LONG, 1, len(thumb))])
    assert len(out) == data_start
    out += blobs
    out += b"\0" * raw_padding                              # stands for the large RAW data after the preview
    Path(path).write_bytes(bytes(out))
    return {"preview_end": prev_off + len(prev)}


# --------------------------------------------------------------------------- MEDIAPRO.XML / clip XML
ZERO_UMID = "0" * 64

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\r\n'
        '<MediaProfile xmlns="http://xmlns.sony.net/pro/metadata/mediaprofile" createdAt="2026-01-02T03:04:05+00:00" version="2.10">\r\n'
        '\t<Properties>\r\n'
        '\t\t<System systemId="0000000000000000" systemKind="ZV-E10" masterVersion="XAVC-M4@1.10.00"/>\r\n'
        '\t\t<Attached mediaId="00000000000000000000000000000000" mediaKind="AffordableMemoryCard" mediaName=""/>\r\n'
        '\t</Properties>\r\n')


def material(cid: str) -> str:
    return (f'\t\t<Material uri="./CLIP/{cid}.MP4" type="MP4" videoType="AVC_3840_2160_HP@L51" audioType="LPCM16" '
            f'fps="25p" dur="144" ch="2" aspectRatio="16:9" offset="0" umid="{ZERO_UMID}">\r\n'
            f'\t\t\t<RelevantInfo uri="./CLIP/{cid}M01.XML" type="XML"/>\r\n'
            f'\t\t\t<RelevantInfo uri="./THMBNL/{cid}T01.JPG" type="JPG"/>\r\n\t\t</Material>\r\n')


def catalog(clips) -> str:
    """MEDIAPRO.XML text for the given clips ([] = how a freshly formatted card looks)."""
    body = ("\t<Contents>\r\n" + "".join(material(c) for c in clips) + "\t</Contents>\r\n") if clips else ""
    return HEAD + body + "</MediaProfile>\r\n"


def clip_xml(frames: int = 336, fps: str = "25p", width: int = 3840, height: int = 2160,
             gamma: str = "ex-cine2", primaries: str = "rec709") -> str:
    """Per-clip metadata sidecar (CnnnnM01.XML)."""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20" lastUpdate="2026-01-02T03:04:05+00:00">
\t<TargetMaterial umidRef="{ZERO_UMID}"/>
\t<Duration value="{frames}"/>
\t<CreationDate value="2026-01-02T03:04:05+00:00"/>
\t<VideoFormat>
\t\t<VideoFrame videoCodec="AVC_{width}_{height}_HP@L51" captureFps="{fps}" formatFps="{fps}"/>
\t\t<VideoLayout pixel="{width}" numOfVerticalLine="{height}" aspectRatio="16:9"/>
\t</VideoFormat>
\t<Device manufacturer="Sony" modelName="ZV-E10" serialNo="0"/>
\t<AcquisitionRecord>
\t\t<Group name="CameraUnitMetadataSet">
\t\t\t<Item name="CaptureGammaEquation" value="{gamma}"/>
\t\t\t<Item name="CaptureColorPrimaries" value="{primaries}"/>
\t\t</Group>
\t</AcquisitionRecord>
</NonRealTimeMeta>
'''


# --------------------------------------------------------------------------- a whole fake card
def make_card(root, photos=(), clips=(), raw_pairs=False, proxies=False, t0=1_762_700_000) -> Path:
    """Create a ZV-E10-like card folder. Photos/clips get distinct modification times starting at t0."""
    root = Path(root)
    for d in ["AVF_INFO", "PRIVATE/M4ROOT/CLIP", "PRIVATE/M4ROOT/THMBNL", "PRIVATE/M4ROOT/SUB",
              "PRIVATE/M4ROOT/GENERAL", "PRIVATE/SONY/SETTING/E10", "DCIM/100MSDCF"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    for n in ["AVIN0001.INP", "AVIN0001.BNP", "AVIN0001.INT", "PRV00001.BIN"]:
        (root / "AVF_INFO" / n).write_bytes(b"db" + n.encode() * 10)
    (root / "PRIVATE/SONY/SETTING/E10/CAMPR01P.DAT").write_bytes(b"preset" * 100)
    (root / "PRIVATE/M4ROOT/STATUS.BIN").write_bytes(b"\x01\x01\0\0\0\0\0")
    for i, p in enumerate(photos):
        f = root / f"DCIM/100MSDCF/{p}.ARW"
        f.write_bytes(os.urandom(5000 + i))
        os.utime(f, (t0 + i, t0 + i))
        if raw_pairs:
            j = root / f"DCIM/100MSDCF/{p}.JPG"
            j.write_bytes(os.urandom(900))
            os.utime(j, (t0 + i, t0 + i))
    for i, c in enumerate(clips):
        m = root / f"PRIVATE/M4ROOT/CLIP/{c}.MP4"
        m.write_bytes(os.urandom(20000 + i))
        os.utime(m, (t0 + 100 + i, t0 + 100 + i))
        (root / f"PRIVATE/M4ROOT/CLIP/{c}M01.XML").write_text(clip_xml(), encoding="utf-8")
        (root / f"PRIVATE/M4ROOT/THMBNL/{c}T01.JPG").write_bytes(jpeg_bytes((320, 180)))
        if proxies:
            (root / f"PRIVATE/M4ROOT/SUB/{c}S03.MP4").write_bytes(os.urandom(700))
    (root / "PRIVATE/M4ROOT/MEDIAPRO.XML").write_bytes(catalog(clips).encode())
    return root
