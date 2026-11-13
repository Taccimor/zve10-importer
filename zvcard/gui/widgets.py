"""Reusable widgets: thumbnail grid with checkboxes, image view, histogram, click-to-seek slider."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QLabel, QListView, QSizePolicy, QSlider, QStyle, QStyledItemDelegate, QStyleOptionSlider, QWidget,
)


# --------------------------------------------------------------------------- thumbnail grid
# Selection is done with CHECKBOXES stored on the items, not with the view's highlight: the highlight
# only marks the item being previewed, so previewing never loses what you have checked.
CHECK_ROLE = int(Qt.ItemDataRole.UserRole) + 1      # item data role holding the checked state (bool)
CHECK_SIZE = 22                                     # drawn box, pixels
CHECK_PAD = 8                                       # distance from the item's top-left corner
CHECK_HIT = 34                                      # clickable square (a bit larger than the box)


def checkbox_rect(item_rect: QRect) -> QRect:
    return QRect(item_rect.x() + CHECK_PAD, item_rect.y() + CHECK_PAD, CHECK_SIZE, CHECK_SIZE)


def checkbox_hit_rect(item_rect: QRect) -> QRect:
    return QRect(item_rect.x() + CHECK_PAD - 6, item_rect.y() + CHECK_PAD - 6, CHECK_HIT, CHECK_HIT)


def click_mode(on_checkbox: bool, ctrl: bool, shift: bool) -> str:
    """What a click on a thumbnail means. Modifier keys win over the checkbox area, so Shift+click and
    Ctrl+click behave the same whether you hit the checkbox or the picture.
    range  : Shift  -> check every item from the last clicked one to this one
    toggle : Ctrl   -> toggle this item's checkbox
    box    : click on the checkbox itself (no modifier) -> toggle it
    plain  : click on the picture (no modifier)         -> only preview it, checks untouched
    (Clicks on the checkbox area never change the preview, whatever the modifiers.)"""
    if shift:
        return "range"
    if ctrl:
        return "toggle"
    if on_checkbox:
        return "box"
    return "plain"


def draw_checkbox(painter: QPainter, rect: QRect, checked: bool) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor(255, 255, 255, 235), 2))
    painter.setBrush(QColor(38, 110, 190) if checked else QColor(0, 0, 0, 130))
    painter.drawRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 4, 4)
    if checked:
        pen = QPen(QColor(255, 255, 255), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x, y, w = rect.x(), rect.y(), rect.width()
        painter.drawPolyline(QPolygonF([QPointF(x + w * 0.24, y + w * 0.52), QPointF(x + w * 0.43, y + w * 0.72),
                                        QPointF(x + w * 0.77, y + w * 0.30)]))
    painter.restore()


class ThumbDelegate(QStyledItemDelegate):
    """Paints the normal icon + text, then the checkbox in the top-left corner of the thumbnail."""
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        draw_checkbox(painter, checkbox_rect(option.rect), bool(index.data(CHECK_ROLE)))


class ThumbGrid(QListView):
    """Icon grid that reports clicks as (row, mode) -- see click_mode -- instead of selecting items."""
    item_clicked = Signal(int, str)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        pos = e.position().toPoint()
        idx = self.indexAt(pos)
        if not idx.isValid():
            super().mousePressEvent(e)
            return
        mods = e.modifiers()
        on_box = checkbox_hit_rect(self.visualRect(idx)).contains(pos)
        mode = click_mode(on_box, bool(mods & Qt.KeyboardModifier.ControlModifier),
                          bool(mods & Qt.KeyboardModifier.ShiftModifier))
        self.item_clicked.emit(idx.row(), mode)   # first: the window still knows which item was shown before
        if not on_box:
            self.setCurrentIndex(idx)             # highlight + preview (never when clicking the checkbox)
        e.accept()

    def mouseDoubleClickEvent(self, e):
        self.mousePressEvent(e)               # a fast second click on a checkbox must toggle again


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
