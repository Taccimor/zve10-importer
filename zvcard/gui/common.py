"""Small helpers shared by the GUI modules: image conversion, formatting, background tasks."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage


THUMB = 220


def pil_to_qimage(img) -> QImage:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    return QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888).copy()


def qimage_to_pil(img: QImage) -> Image.Image:
    """QImage (RGB888) -> PIL image, honouring the row padding Qt adds."""
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h, stride = img.width(), img.height(), img.bytesPerLine()
    try:
        data = bytes(img.constBits())
        if len(data) >= stride * h:
            return Image.frombuffer("RGB", (w, h), data, "raw", "RGB", stride, 1)
    except (TypeError, ValueError):
        pass
    # fallback (slower): go through an in-memory BMP
    import io
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "BMP")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000


def fmt_ms(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


def backup_dir() -> Path:
    import os
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "ZVCardTool" / "backups"


class _Signals(QObject):
    done = Signal(object, object, object, object)      # task, callback, result, error


class Task(QRunnable):
    """Run fn() in a pool thread; the result is delivered in the GUI thread."""
    def __init__(self, fn, callback):
        super().__init__()
        self.fn, self.callback = fn, callback
        self.signals = _Signals()

    def run(self):
        try:
            res, err = self.fn(), None
        except BaseException as e:  # noqa: BLE001
            res, err = None, e
        self.signals.done.emit(self, self.callback, res, err)
