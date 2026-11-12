"""Video frame pipeline: LUT grading and live histogram off the GUI thread (see FrameTap)."""
from __future__ import annotations

import threading
import time

from PIL import Image
from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoSink

from .. import luts, media
from .common import qimage_to_pil


class FrameTap(QObject):
    """Receives video frames and produces (a) LUT-graded images and (b) live histograms.

    Pipeline, built so the window never stutters:
      * event driven: a new frame wakes the GUI thread (no fixed timer -> no frame-timing jitter);
      * the GUI thread only does the cheap part (QVideoFrame -> small RGB image);
      * the expensive part (resize + LUT + histogram) runs in ONE worker thread that always takes the
        NEWEST job (older ones are dropped, never queued up);
      * frames are graded at the size they are displayed at (and automatically smaller if the computer
        cannot keep up), so no time is wasted on pixels nobody sees.
    """
    image_ready = Signal(object)        # QImage (LUT mode)
    hist_ready = Signal(object)         # histogram dict
    perf_ready = Signal(str)            # "24 fps, 900 px" (LUT mode)
    wake = Signal()
    result = Signal(object)

    LEVELS = (1920, 1600, 1280, 1024, 800, 640)     # automatic quality steps (max processing width)
    BUDGET_MS = 28.0                                 # per-frame time we try to stay under

    def __init__(self, size_hint, parent=None):
        super().__init__(parent)
        self.sink = QVideoSink(self)
        self.size_hint = size_hint      # callable -> (width, height) of the display area
        self.latest = None              # newest frame not handled yet
        self._last = None               # most recent frame (re-used when the picture is paused)
        self._dirty = True              # a histogram for _last is still owed
        self._redraw = False            # the LUT picture must be re-rendered from _last
        self._last_hist = 0.0
        self._wake_pending = False
        self.lut = None                 # luts.Lut or None
        self.show_processed = False     # True when frames are displayed by us (LUT mode)
        self.synchronous = False        # tests: run the worker inline
        self.level = 0
        self._over = self._under = 0
        self._frames, self._t0 = 0, time.monotonic()
        self._cv = threading.Condition()
        self._job = None                # newest waiting job (single slot)
        self._running = False
        self._stop = False
        self._thread = None
        self.timer = QTimer(self)       # only a safety net for "frames stopped" (paused / seeked)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.idle_tick)
        self.sink.videoFrameChanged.connect(self.on_frame)
        self.wake.connect(self._on_wake)
        self.result.connect(self._on_result)

    # ---------------------------------------------------------------- frame arrival
    def on_frame(self, frame):
        # may run in the video thread: store the frame and wake the GUI thread, touch no widgets
        self.latest = frame
        if not self._wake_pending:
            self._wake_pending = True
            self.wake.emit()

    @Slot()
    def _on_wake(self):
        self._wake_pending = False
        self._process()

    def reset(self):
        self.latest = self._last = None
        self._dirty = True
        self._redraw = False

    def invalidate(self):
        """Settings changed (LUT on/off...): re-render picture + histogram of the current frame."""
        self._dirty = True
        self._redraw = True
        self._frames, self._t0 = 0, time.monotonic()

    def stop(self):
        with self._cv:
            self._stop = True
            self._cv.notify()

    # ---------------------------------------------------------------- scheduling
    def _can_submit(self) -> bool:
        with self._cv:
            return not (self._running and self._job is not None)

    def _process(self):
        frame = self.latest
        if frame is None:
            return
        now = time.monotonic()
        self._last = frame
        self._dirty = True
        need_img = self.show_processed
        want_hist = now - self._last_hist >= 0.12           # at most ~8 histograms per second
        if not (need_img or want_hist):
            self.latest = None                              # idle_tick delivers the histogram later
            return
        if not self._can_submit():
            return                                          # keep the frame; retried after the worker finishes
        self.latest = None
        self._submit(frame, need_img, want_hist, now)

    def idle_tick(self):
        """Frames stopped (paused/seeked) or a setting changed: make sure the last picture is
        graded and has its histogram."""
        if self.latest is not None:
            self._process()
            return
        if self._last is None or not (self._dirty or self._redraw) or not self._can_submit():
            return
        self._submit(self._last, self.show_processed and self._redraw, True, time.monotonic())

    def _submit(self, frame, need_img: bool, want_hist: bool, now: float):
        job = self._prepare(frame, need_img)
        if job is None:
            return
        job.update(need_img=need_img, want_hist=want_hist,
                   lut=self.lut if self.show_processed else None)
        if want_hist:
            self._last_hist, self._dirty = now, False
        if need_img:
            self._redraw = False
        if self.synchronous:
            self._on_result(self._run(job))
            return
        with self._cv:
            self._job = job                                  # replaces an older waiting job
            self._cv.notify()
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def _prepare(self, frame, need_img: bool):
        """GUI-thread part: frame -> small RGB PIL image (4K frames are downscaled first)."""
        t0 = time.perf_counter()
        img = frame.toImage()
        if img.isNull():
            return None
        if need_img:
            hw, hh = self.size_hint()
            aspect = img.width() / max(1, img.height())
            tw = int(max(320, min(hw, hh * aspect, self.LEVELS[self.level])))
        else:
            tw = 320
        src_w = min(img.width(), tw * 2)                     # PIL does the final, nicer reduction
        if img.width() > src_w:
            img = img.scaledToWidth(src_w, Qt.TransformationMode.FastTransformation)
        pil = qimage_to_pil(img)
        th = max(2, round(tw * pil.height / max(1, pil.width)))
        return {"pil": pil, "tw": tw, "th": th, "prep_ms": (time.perf_counter() - t0) * 1000}

    # ---------------------------------------------------------------- worker thread
    def _worker(self):
        while True:
            with self._cv:
                while self._job is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                job, self._job = self._job, None
                self._running = True
            res = self._run(job)
            with self._cv:
                self._running = False
            self.result.emit(res)

    def _run(self, job):
        t0 = time.perf_counter()
        try:
            pil = job["pil"]
            if pil.width != job["tw"]:
                pil = pil.resize((job["tw"], job["th"]), Image.Resampling.BILINEAR)
            if job["lut"] is not None:
                pil = luts.apply_lut(pil, job["lut"])
            img = (pil.width, pil.height, pil.tobytes()) if job["need_img"] else None
            hist = media.histogram(pil, 320) if job["want_hist"] else None
            return {"img": img, "hist": hist, "prep_ms": job["prep_ms"],
                    "work_ms": (time.perf_counter() - t0) * 1000}
        except Exception:  # noqa: BLE001  (a bad frame must never crash the window)
            return None

    # ---------------------------------------------------------------- back in the GUI thread
    @Slot(object)
    def _on_result(self, res):
        if res is not None:
            if res["img"] is not None:
                w, h, data = res["img"]
                self.image_ready.emit(QImage(data, w, h, w * 3, QImage.Format.Format_RGB888).copy())
                self._adapt(max(res["prep_ms"], res["work_ms"]), w)
            if res["hist"] is not None:
                self.hist_ready.emit(res["hist"])
        self._process()                                      # a frame may have arrived meanwhile

    def _adapt(self, stage_ms: float, width: int):
        """Lower the processing size if frames take too long; raise it again when there is headroom."""
        if stage_ms > self.BUDGET_MS:
            self._over, self._under = self._over + 1, 0
        elif stage_ms < self.BUDGET_MS * 0.45:
            self._under, self._over = self._under + 1, 0
        else:
            self._over = self._under = 0
        if self._over >= 8 and self.level < len(self.LEVELS) - 1:
            self.level, self._over = self.level + 1, 0
        elif self._under >= 60 and self.level > 0:
            self.level, self._under = self.level - 1, 0
        self._frames += 1
        now = time.monotonic()
        if now - self._t0 >= 1.0:
            self.perf_ready.emit(f"LUT preview: {self._frames / (now - self._t0):.0f} fps at {width} px")
            self._frames, self._t0 = 0, now
