"""A tiny stand-in for PySide6 so the GUI *logic* can be smoke-tested without Qt.
It checks my Python code (names, flow, threading hand-offs); it can NOT check real Qt behaviour."""
import threading

class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        d = cls.__dict__.get("_c")
        if d is None:
            d = type.__setattr__(cls, "_c", {}) or cls.__dict__["_c"]
        if name not in d:
            d[name] = type(name, (D,), {})
        return d[name]

class D(metaclass=_Meta):
    """Generic dummy: accepts anything, chains attributes, is empty when iterated."""
    def __init__(self, *a, **k): self._children = {}
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        ch = self.__dict__.setdefault("_children", {})
        if name not in ch:
            ch[name] = D()
        return ch[name]
    def __call__(self, *a, **k): return D()
    def __iter__(self): return iter(())
    def __bool__(self): return True
    def __or__(self, o): return self
    def __enter__(self): return self
    def __exit__(self, *a): return False

class Signal:
    def __init__(self, *types): self.name = None
    def __set_name__(self, owner, name): self.name = name
    def __get__(self, obj, owner=None):
        if obj is None: return self
        store = obj.__dict__.setdefault("_signals", {})
        if self.name not in store: store[self.name] = _BoundSignal()
        return store[self.name]

class _BoundSignal:
    def __init__(self): self.handlers = []
    def connect(self, fn): self.handlers.append(fn)
    def emit(self, *a):
        for h in list(self.handlers): h(*a)
    def __call__(self, *a): self.emit(*a)

def Slot(*types):
    return lambda fn: fn

class QObject(D):
    def __init__(self, *a, **k): super().__init__()
    def deleteLater(self): pass

class QRunnable(D):
    def __init__(self): super().__init__()

class QThreadPool(D):
    _g = None
    @classmethod
    def globalInstance(cls):
        if cls._g is None: cls._g = cls()
        return cls._g
    def setMaxThreadCount(self, n): pass
    def start(self, task): task.run()          # synchronous: deterministic tests

class QTimer(QObject):
    def __init__(self, *a): super().__init__(); self.timeout = _BoundSignal()
    def setSingleShot(self, b): pass
    def start(self, ms=0): self.started = ms

class QSettings(D):
    store = {}
    def __init__(self, *a): super().__init__()
    def value(self, key, default=None, type=None): return self.store.get(key, default)
    def setValue(self, key, v): self.store[key] = v

class QStandardItem(D):
    def __init__(self, text=""): super().__init__(); self._text, self._data, self._icon = text, {}, None
    def setEditable(self, b): pass
    def setIcon(self, i): self._icon = i
    def icon(self): return self._icon
    def text(self): return self._text
    def setData(self, v, role): self._data[role] = v
    def data(self, role): return self._data.get(role)

class _Idx:
    def __init__(self, r): self._r = r
    def row(self): return self._r
    def isValid(self): return self._r >= 0

class QStandardItemModel(D):
    def __init__(self): super().__init__(); self.rows = []
    def clear(self): self.rows = []
    def appendRow(self, it): self.rows.append(it)
    def rowCount(self): return len(self.rows)
    def item(self, r): return self.rows[r]
    def index(self, r, c): return _Idx(r)

class _SelModel(D):
    def __init__(self): super().__init__(); self.sel = set(); self.selectionChanged = _BoundSignal(); self.currentChanged = _BoundSignal()
    def clearSelection(self): self.sel.clear(); self.selectionChanged.emit()
    def select(self, idx, flag): self.sel.add(idx.row()); self.selectionChanged.emit()
    def selectedIndexes(self): return [_Idx(r) for r in sorted(self.sel)]
    def setCurrentIndex(self, idx, flag): self.cur = idx.row()

class QListView(D):
    def __init__(self): super().__init__(); self._model = None; self._hidden = set(); self._sm = _SelModel(); self._cur = -1
    def setModel(self, m): self._model = m
    def selectionModel(self): return self._sm
    def setRowHidden(self, r, h): (self._hidden.add if h else self._hidden.discard)(r)
    def isRowHidden(self, r): return r in self._hidden
    def clearSelection(self): self._sm.clearSelection()
    def currentIndex(self): return _Idx(self._cur)

class QComboBox(D):
    def __init__(self): super().__init__(); self.items = []; self.idx = 0; self.activated = _BoundSignal(); self.currentIndexChanged = _BoundSignal()
    def addItem(self, text, data=None): self.items.append((text, data))
    def clear(self): self.items = []
    def findData(self, d):
        for i, (_, x) in enumerate(self.items):
            if x == d: return i
        return -1
    def currentData(self): return self.items[self.idx][1] if self.items else None
    def currentIndex(self): return self.idx
    def setCurrentIndex(self, i): self.idx = i
    def setMinimumWidth(self, w): pass
    def setEnabled(self, b): pass

class QLabel(D):
    def __init__(self, text=""): super().__init__(); self.t = text
    def size(self): return QSize(700, 400)
    def width(self): return 700
    def height(self): return 400
    def setText(self, t): self.t = t
    def text(self): return self.t
class QPushButton(D):
    visible = True
    def setVisible(self, v): self.visible = bool(v)
    def __init__(self, text=""): super().__init__(); self.enabled = True; self.clicked = _BoundSignal(); self.t = text
    def setEnabled(self, b): self.enabled = bool(b)
    def setText(self, t): self.t = t
class QLineEdit(D):
    def __init__(self, text=""): super().__init__(); self.t = str(text)
    def text(self): return self.t
    def setText(self, t): self.t = t
class QCheckBox(D):
    def setVisible(self, v): self.vis = bool(v)
    def isVisible(self): return getattr(self, 'vis', True)
    def __init__(self, text=""): super().__init__(); self.c = False; self.toggled = _BoundSignal()
    def setChecked(self, b): self.c = bool(b)
    def isChecked(self): return self.c
    def setEnabled(self, b): self.en = bool(b)
    def toggle(self): self.c = not self.c; self.toggled.emit(self.c)
    def setText(self, t): self.t = t
class QDialog(D):
    result_value = 1
    def __init__(self, parent=None): super().__init__()
    def exec(self): return QDialog.result_value
    def accept(self): pass
    def reject(self): pass
class QDialogButtonBox(D):
    class StandardButton: Ok = 1; Cancel = 2
    def __init__(self, *a): super().__init__(); self.accepted = _BoundSignal(); self.rejected = _BoundSignal(); self._b = QPushButton()
    def button(self, b): return self._b
class QMainWindow(QObject):
    def __init__(self): super().__init__(); self._sb = D()
    def statusBar(self): return _SB
    def setCentralWidget(self, w): pass
    def resize(self, *a): pass
    def setWindowTitle(self, t): pass
    def addAction(self, a): pass
class _StatusBar:
    msg = ""
    def showMessage(self, m): _StatusBar.msg = m
_SB = _StatusBar()
class QWidget(QObject):
    def close(self):
        if hasattr(type(self), 'closeEvent'): self.closeEvent(None)
        return True
    def showFullScreen(self): self.full = True
    def setVisible(self, v): self.vis = bool(v)
    def isVisible(self): return getattr(self, 'vis', True)
    def closeEvent(self, e): pass
    def keyPressEvent(self, e): pass
    def resizeEvent(self, e): pass
class QMessageBox(D):
    class StandardButton: Yes = 1; No = 2
    log = []
    @staticmethod
    def question(parent, title, text, *a): QMessageBox.log.append(("question", title, text)); return 1
    @staticmethod
    def warning(parent, title, text, *a): QMessageBox.log.append(("warning", title, text)); return 1
    @staticmethod
    def information(parent, title, text, *a): QMessageBox.log.append(("info", title, text)); return 1
    @staticmethod
    def critical(parent, title, text, *a): QMessageBox.log.append(("critical", title, text)); return 1
class QProgressDialog(D):
    def __init__(self, *a): super().__init__(); self.canceled = _BoundSignal(); self.closed = False; self.last = None
    def setValue(self, v): self.last = v
    def setLabelText(self, t): self.label = t
    def close(self): self.closed = True
class QFileDialog(D):
    @staticmethod
    def getExistingDirectory(*a): return ""
class QMediaPlayer(QObject):
    class PlaybackState: PlayingState = "playing"; PausedState = "paused"
    def __init__(self, *a): super().__init__(); self.positionChanged = _BoundSignal(); self.durationChanged = _BoundSignal(); self.playbackStateChanged = _BoundSignal(); self.src = None; self.state = "paused"
    def setAudioOutput(self, a): pass
    def setVideoOutput(self, a): pass
    def stop(self): self.src = None
    def setSource(self, u): self.src = u if getattr(u, 's', '') else None
    def pause(self): self.state = "paused"
    def play(self): self.state = "playing"
    def playbackState(self): return self.state
    def setPosition(self, p): self.pos = p
    def position(self): return getattr(self, 'pos', 0)
    def setPlaybackRate(self, r): self.rate = r
    def setVideoOutput(self, o): self.output = o
class QUrl(D):
    def __init__(self, s=""): super().__init__(); self.s = s
    @staticmethod
    def fromLocalFile(p): return QUrl(p)
class QImage(D):
    class Format: Format_RGBA8888 = 1; Format_RGB888 = 2
    def __init__(self, data=None, w=0, h=0, stride=0, fmt=None): super().__init__(); self.w, self.h = w, h
    def copy(self): return self
class QPixmap(D):
    def __init__(self, *a): super().__init__()
    def size(self): return QSize(1280, 720)
    def width(self): return 1280
    def height(self): return 720
    def scaled(self, *a): return self
    @staticmethod
    def fromImage(i): return QPixmap()
    def fill(self, c): pass
class QStackedWidget(D):
    def __init__(self): super().__init__(); self.i = 0
    def setCurrentIndex(self, i): self.i = i
    def currentIndex(self): return self.i
    def addWidget(self, w): pass
class QSlider(D):
    def __init__(self, *a): super().__init__(); self.sliderMoved = _BoundSignal()
    def isSliderDown(self): return False
class QApplication(D): pass
class QAction(D):
    def __init__(self, *a): super().__init__(); self.triggered = _BoundSignal()

class QVideoSink(QObject):
    def __init__(self, *a): super().__init__(); self.videoFrameChanged = _BoundSignal()
class QVideoWidget(QWidget):
    def __init__(self): super().__init__(); self._sink = QVideoSink()
    def videoSink(self): return self._sink
class QTreeWidgetItem(D):
    def __init__(self, texts=None): super().__init__(); self.kids = []; self.data_ = {}
    def addChild(self, c): self.kids.append(c)
    def setToolTip(self, *a): pass
    def setData(self, col, role, v): self.data_[(col, role)] = v
    def setExpanded(self, b): pass
class QTreeWidget(D):
    def __init__(self): super().__init__(); self.tops = []; self.itemDoubleClicked = _BoundSignal()
    def clear(self): self.tops = []
    def addTopLevelItem(self, i): self.tops.append(i)
    def selectedItems(self): return []
class QVBoxLayout(D):
    def __init__(self, *a): super().__init__(); self.widgets = []
    def addWidget(self, w, *a): self.widgets.append(w)
    def removeWidget(self, w):
        if w in self.widgets: self.widgets.remove(w)
    def insertWidget(self, i, w, *a): self.widgets.insert(i, w)

class QAudioOutput(QObject):
    def __init__(self, *a): super().__init__(); self.muted = False
    def setVolume(self, v): pass
    def setMuted(self, m): self.muted = bool(m)
class QCheckBoxToggle: pass

class QSize:
    def __init__(self, w=0, h=0): self._w, self._h = w, h
    def width(self): return self._w
    def height(self): return self._h
    def scaled(self, other, mode=None):
        r = min(other.width() / max(1, self._w), other.height() / max(1, self._h))
        return QSize(round(self._w * r), round(self._h * r))
