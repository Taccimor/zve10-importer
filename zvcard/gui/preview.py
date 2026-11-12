"""Preview pane (photo / video / LUT / slow motion) and the full-screen window that hosts it."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import media
from .common import fmt_ms
from .pipeline import FrameTap
from .widgets import ClickSlider, HistogramWidget, ImageView


class PreviewPane(QWidget):
    """Photo (image) or video (player) preview with info text, histogram, LUT and video tools."""
    fullscreen_requested = Signal()
    lut_button_clicked = Signal()
    lut_state_changed = Signal()
    conform_toggled = Signal(bool)

    def __init__(self, conform: bool = False):
        super().__init__()
        # The media player is created lazily (first video): QtMultimedia + FFmpeg cost a lot of memory.
        self.player = None
        self.audio = None
        self.tap = None
        self._duration = 0
        self._fps: float | None = None
        self._lut = None
        self._lut_active = False
        self._chrome = True

        self.stack = QStackedWidget()
        self.hint = QLabel("Select a photo or video")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image = ImageView()
        self.video = QVideoWidget()
        self.lutview = ImageView()
        self.surface = QStackedWidget()
        self.surface.addWidget(self.video)
        self.surface.addWidget(self.lutview)
        self.play_btn = QPushButton("Play")
        self.play_btn.setFixedWidth(70)
        self.slider = ClickSlider(Qt.Orientation.Horizontal)
        self.time = QLabel("0:00 / 0:00")
        controls = QWidget()
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(0, 0, 0, 0)
        for w, st in ((self.play_btn, 0), (self.slider, 1), (self.time, 0)):
            cl.addWidget(w, st)
        vpage = QWidget()
        vl = QVBoxLayout(vpage)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.surface, 1)
        vl.addWidget(controls)
        for w in (self.hint, self.image, vpage):
            self.stack.addWidget(w)

        # slow motion: only offered for high-frame-rate clips
        self.conform_box = QCheckBox("Slow motion")
        self.conform_box.setChecked(conform)
        self.tools = QWidget()
        tl = QHBoxLayout(self.tools)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.conform_box)
        tl.addStretch(1)
        self.tools.setVisible(False)

        # LUT row (photos and videos)
        self.lut_btn = QPushButton("LUT...")
        self.lut_label = QLabel("")
        self.lut_perf = QLabel("")
        self.lut_perf.setStyleSheet("color: gray;")
        self.use_lut = QCheckBox("Use LUT  (L)")
        self.use_lut.setChecked(True)
        self.use_lut.setVisible(False)
        self.lut_row = QWidget()
        ll = QHBoxLayout(self.lut_row)
        ll.setContentsMargins(0, 0, 0, 0)
        for w in (self.lut_btn, self.lut_label, self.use_lut):
            ll.addWidget(w)
        ll.addStretch(1)
        ll.addWidget(self.lut_perf)

        self.fs_btn = QPushButton("Full screen (F)")
        self.info = QLabel()
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.hist = HistogramWidget()
        self.hist_caption = QLabel("")
        header = QHBoxLayout()
        header.addStretch(1)
        header.addWidget(self.fs_btn)
        self.lay = QVBoxLayout(self)
        self.header = QWidget()
        self.header.setLayout(header)
        self.lay.addWidget(self.header)
        self.lay.addWidget(self.stack, 1)
        self.lay.addWidget(self.tools)
        self.lay.addWidget(self.lut_row)
        self.lay.addWidget(self.info)
        self.lay.addWidget(self.hist_caption)
        self.lay.addWidget(self.hist)

        for w in (self.play_btn, self.slider, self.conform_box, self.lut_btn, self.use_lut, self.fs_btn):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)    # keys must reach the window, not a button
        self.play_btn.clicked.connect(self.toggle_play)
        self.slider.seek_requested.connect(self._seek)
        self.fs_btn.clicked.connect(self.fullscreen_requested)
        self.lut_btn.clicked.connect(self.lut_button_clicked)
        self.use_lut.toggled.connect(self._lut_toggled)
        self.conform_box.toggled.connect(self._conform_changed)

    # ------------------------------------------------------------ player (lazy)
    def _ensure_player(self):
        if self.player is not None:
            return
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.tap = FrameTap(self._display_size, self)
        self.tap.image_ready.connect(self.lutview.set_image)
        self.tap.perf_ready.connect(self.lut_perf.setText)
        self.tap.hist_ready.connect(self._on_live_hist)
        try:                                # the histogram taps the frames the video widget shows
            self.video.videoSink().videoFrameChanged.connect(self.tap.on_frame)
        except Exception:  # noqa: BLE001
            pass
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.playbackStateChanged.connect(self._on_state)
        self._route_output()

    def _display_size(self):
        """Pixels available for the picture (the tap grades frames at this size, not bigger)."""
        return max(64, self.lutview.width()), max(64, self.lutview.height())

    def _route_output(self):
        """Plain GPU video widget normally; our own frame display when a LUT is active."""
        if self.player is None:
            return
        self.tap.lut = self._lut if self._lut_active else None
        self.tap.show_processed = self._lut_active
        self.lut_perf.setText("")
        if self._lut_active:
            self.player.setVideoOutput(self.tap.sink)
            self.surface.setCurrentIndex(1)
        else:
            self.player.setVideoOutput(self.video)
            self.surface.setCurrentIndex(0)
        self.tap.invalidate()
        self.player.setPosition(self.player.position())     # ask for a fresh frame

    # ------------------------------------------------------------ controls
    def toggle_play(self):
        if self.stack.currentIndex() != 2 or self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek_by(self, ms: int):
        if self.player is not None and self.stack.currentIndex() == 2:
            self.player.setPosition(max(0, min(self._duration, self.player.position() + ms)))

    def _seek(self, pos):
        if self.player is not None:
            self.player.setPosition(pos)        # keeps playing if playing, stays paused if paused

    def _on_pos(self, pos):
        if not self.slider.dragging:
            self.slider.setValue(pos)
        self.time.setText(f"{fmt_ms(pos)} / {fmt_ms(self._duration)}")

    def _on_dur(self, dur):
        self._duration = dur
        self.slider.setRange(0, dur)

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("Pause" if playing else "Play")

    # ------------------------------------------------------------ slow motion ("conform")
    def _apply_conform(self):
        ratio = media.conform_ratio(self._fps)
        if ratio is not None:
            self.conform_box.setText(f"Slow motion: play {self._fps:g} fps clip at {self._fps * ratio:g} fps  (S)")
        on = ratio is not None and self.conform_box.isChecked()
        if self.player is not None:
            self.player.setPlaybackRate(ratio if on else 1.0)
            self.audio.setMuted(on)                     # slowed audio is just noise
        self._refresh_tools()

    def _refresh_tools(self):
        # the checkbox only makes sense for 50/60/100/120 fps clips
        self.tools.setVisible(self._chrome and self.stack.currentIndex() == 2
                              and media.conform_ratio(self._fps) is not None)

    def _conform_changed(self, checked):
        self._apply_conform()
        self.conform_toggled.emit(bool(checked))

    # ------------------------------------------------------------ histogram / LUT
    def _on_live_hist(self, data):
        self.hist.set_data(data)
        self.hist_caption.setText("Histogram (live" + (", with LUT)" if self._lut_active else ")"))

    def lut_in_use(self):
        return self._lut if self._lut_active else None

    def set_lut(self, lut, name: str = "", tooltip: str = ""):
        self._lut = lut
        self.lut_label.setText(name)
        self.lut_label.setToolTip(tooltip)
        self.use_lut.setVisible(lut is not None)         # nothing to switch without a LUT
        self._lut_active = lut is not None and self.use_lut.isChecked()
        self._route_output()
        self.lut_state_changed.emit()

    def _lut_toggled(self, checked):
        self._lut_active = self._lut is not None and bool(checked)
        self._route_output()
        self.lut_state_changed.emit()

    # ------------------------------------------------------------ used by the main window
    def release(self):
        """Close every file we have open (needed before deleting / resetting the card)."""
        if self.player is not None:
            self.tap.timer.stop()
            self.tap.reset()
            self.player.stop()
            self.player.setSource(QUrl())
        self.image.set_image(None)
        self.lutview.set_image(None)
        self.lut_perf.setText("")
        self.stack.setCurrentIndex(0)
        self.info.setText("")
        self.hist.set_data(None)
        self.hist_caption.setText("")
        self._refresh_tools()

    def show_message(self, text: str):
        self.hint.setText(text)
        self.stack.setCurrentIndex(0)
        self._refresh_tools()

    def show_photo(self, qimg: QImage, hist, rows):
        if self.player is not None:
            self.tap.timer.stop()
            self.player.stop()
            self.player.setSource(QUrl())
        self.image.set_image(qimg)
        self.stack.setCurrentIndex(1)
        self.hist.set_data(hist)
        self.hist_caption.setText("Histogram" + (" (with LUT)" if self._lut_active else ""))
        self._set_info(rows)
        self._refresh_tools()

    def show_video(self, path: Path, thumb_hist, rows, fps: float | None):
        self.image.set_image(None)
        self._ensure_player()
        self._fps = fps
        self.tap.reset()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.pause()
        self.stack.setCurrentIndex(2)
        self._apply_conform()
        self.hist.set_data(thumb_hist)
        self.hist_caption.setText("Histogram (live)")
        self.tap.timer.start()
        self._set_info(rows)

    def _set_info(self, rows):
        html = "<table>" + "".join(f"<tr><td><b>{k}&nbsp;</b></td><td>{v}</td></tr>" for k, v in rows) + "</table>"
        self.info.setText(html)

    # -- full screen support: the whole stage (image/video + controls) moves to another window
    def take_stage(self):
        self.lay.removeWidget(self.stack)
        return self.stack

    def restore_stage(self):
        self.lay.insertWidget(1, self.stack, 1)
        self.stack.show()

    def set_chrome_visible(self, visible: bool):
        self._chrome = visible
        for w in (self.header, self.lut_row, self.info, self.hist_caption, self.hist):
            w.setVisible(visible)
        self._refresh_tools()

    def shutdown(self):
        if self.tap is not None:
            self.tap.timer.stop()
            self.tap.stop()


class FullscreenWindow(QWidget):
    """Black full-screen window that hosts the preview stage. Keys: Esc/F exit, Space play/pause,
    Left/Right previous/next item, Shift+Left/Right seek 5 s, S slow motion on/off, L LUT on/off."""
    def __init__(self, stage, on_closed, on_step, on_play, on_seek, on_toggle):
        super().__init__()
        self.setWindowTitle("Preview")
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(stage)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._on_closed, self._on_step, self._on_play, self._on_seek = on_closed, on_step, on_play, on_seek
        self._on_toggle = on_toggle

    def keyPressEvent(self, e):
        k = e.key()
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if k in (Qt.Key.Key_Escape, Qt.Key.Key_F):
            self.close()
        elif k == Qt.Key.Key_Space:
            self._on_play()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            sign = -1 if k == Qt.Key.Key_Left else 1
            self._on_seek(sign * 5000) if shift else self._on_step(sign)
        elif k == Qt.Key.Key_S:
            self._on_toggle("slow")
        elif k == Qt.Key.Key_L:
            self._on_toggle("lut")
        else:
            super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.close()

    def closeEvent(self, e):
        self._on_closed()
        super().closeEvent(e)
