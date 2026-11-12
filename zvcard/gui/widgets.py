"""Reusable widgets: image view, histogram, click-to-seek timeline slider."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QLabel, QSizePolicy, QSlider, QStyle, QStyleOptionSlider, QWidget


class ImageView(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(200, 200)
        self._pm: QPixmap | None = None

    def set_image(self, qimg: QImage | None):
        self._pm = QPixmap.fromImage(qimg) if qimg is not None else None
        self.clear() if self._pm is None else self._rescale()

    def _rescale(self):
        if not self._pm:
            return
        fit = self._pm.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        if abs(fit.width() - self._pm.width()) <= 2 and abs(fit.height() - self._pm.height()) <= 2:
            self.setPixmap(self._pm)            # already display size: skip the (costly) scaling
        else:
            self.setPixmap(self._pm.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale()


class HistogramWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.data = None
        self.setMinimumHeight(90)
        self.setMaximumHeight(130)

    def set_data(self, data):
        self.data = data
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(22, 22, 22))
        if self.data:
            w, h = self.width() - 1, self.height() - 1
            peak = max(max(self.data[k][1:255]) for k in ("r", "g", "b", "luma")) or 1
            layers = (("luma", QColor(210, 210, 210, 70), True), ("r", QColor(255, 70, 70), False),
                      ("g", QColor(70, 220, 70), False), ("b", QColor(90, 130, 255), False))
            for key, color, filled in layers:
                hist = self.data[key]
                pts = [QPointF(i * w / 255, h - min(hist[i], peak) / peak * (h - 4)) for i in range(256)]
                if filled:
                    poly = QPolygonF(pts + [QPointF(w, h), QPointF(0, h)])
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(color))
                    p.drawPolygon(poly)
                else:
                    p.setPen(QPen(color, 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPolyline(QPolygonF(pts))
        p.end()


def slider_value(x: float, groove_left: float, groove_width: float, handle_width: float,
                 vmin: int, vmax: int) -> int:
    """Slider value under mouse x: the click point becomes the CENTRE of the handle."""
    span = max(1.0, groove_width - handle_width)
    pos = min(max(x - groove_left - handle_width / 2, 0.0), span)
    return round(vmin + (vmax - vmin) * pos / span)


class ClickSlider(QSlider):
    """Timeline: a click jumps straight to that point; dragging scrubs."""
    seek_requested = Signal(int)

    def __init__(self, *args):
        super().__init__(*args)
        self.dragging = False

    def _value_at(self, x: float) -> int:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        st = self.style()
        groove = st.subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
        handle = st.subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self)
        return slider_value(x, groove.x(), groove.width(), handle.width(), self.minimum(), self.maximum())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            v = self._value_at(e.position().x())
            self.setValue(v)
            self.seek_requested.emit(v)
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.dragging:
            v = self._value_at(e.position().x())
            self.setValue(v)
            self.seek_requested.emit(v)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self.dragging = False
        super().mouseReleaseEvent(e)
