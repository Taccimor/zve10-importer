"""Headless smoke test of the zvcard.gui package against a FAKE Qt (see tests/fakeqt).

It exercises the window's Python logic end to end on a fake card: selection, filters, previews, import,
delete, offload, LUT, slow motion, full screen, the frame pipeline (with real threads). It cannot prove
that real Qt looks or behaves right: that needs a person at a real screen."""
import os, shutil, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fakeqt")); sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
from fixtures import make_card, make_fake_arw

tmp = Path(tempfile.mkdtemp()); os.environ["APPDATA"] = str(tmp / "appdata")
card = make_card(tmp / "card", ["DSC00001", "DSC00002"], ["C1001"], raw_pairs=False)
make_fake_arw(card / "DCIM/100MSDCF/DSC02997.ARW")

from zvcard import scan
scan.find_cards = lambda: []
from PySide6.QtCore import QSettings
QSettings.store.update({"last_folder": str(card), "dest": str(tmp / "dest")})
from PySide6.QtWidgets import QMessageBox
from zvcard.gui import main_window, pipeline, widgets

def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond: check.failed += 1
check.failed = 0

win = main_window.MainWindow()
check(win.model.rowCount() == 4, f"grid shows 4 items (got {win.model.rowCount()})")
real = [r for r in range(4) if win.item_at(r).name in ("DSC02997", "C1001")]
check(all(win.model.item(r).icon() is not win.placeholder for r in real), "real thumbnails replaced the placeholder")
check(all(win.model.item(r).icon() is win.placeholder for r in range(4) if r not in real), "unreadable files keep the placeholder (no crash)")
check(not QMessageBox.log, "no error boxes on load")

# filters + selection
win.filter_box.idx = [d for _, d in win.filter_box.items].index("video"); win.apply_filter()
check(sum(win.view.isRowHidden(r) for r in range(4)) == 3, "video filter hides the 3 photos")
win.select_where(lambda it: True)
check(len(win.selected_items()) == 1, "'select all' selects only visible rows")
win.filter_box.idx = 0; win.apply_filter(); win.select_where(lambda it: True)
check(len(win.selected_items()) == 4 and win.b_import.enabled and win.b_delete.enabled, "select all -> buttons enabled")
win.view.clearSelection(); win._update_buttons()
check(not win.b_import.enabled and not win.b_delete.enabled and win.b_offload.enabled, "no selection -> import/delete disabled, offload enabled")

# previews
vrow = next(r for r in range(4) if win.item_at(r).kind == "video")
prow = next(r for r in range(4) if win.item_at(r).name == "DSC02997")
win.view._cur = prow; win._load_preview()
check(win.preview.stack.currentIndex() == 1 and "ISO" in win.preview.info.text(), "photo preview shows image + exposure info")
check(win.preview.hist.data is not None, "photo histogram computed")
win.view._cur = vrow; win._load_preview()
check(win.preview.stack.currentIndex() == 2 and win.preview.player.src is not None, "video preview loads the player")

def wait_dialog(n_before, timeout=15):
    t = time.time()
    while len(QMessageBox.log) <= n_before and time.time() - t < timeout: time.sleep(0.05)
    time.sleep(0.2)
    return QMessageBox.log[n_before:] if len(QMessageBox.log) > n_before else []

# import only (keep on card)
win.select_where(lambda it: it.name == "DSC02997")
n = len(QMessageBox.log); win.do_import()
log = wait_dialog(n)
dest_files = sorted(p.name for p in (tmp / "dest").rglob("*") if p.is_file())
check(dest_files == ["DSC02997.ARW"], f"import copied only the selected file ({dest_files})")
check(win.preview.player.src is None, "preview released its file handles before the session")
check((card / "DCIM/100MSDCF/DSC02997.ARW").exists(), "keep-on-card left the file on the card")
check(any("Imported: 1" in m[2] for m in log), "result box reports the import")
check("✓" in win.model.item([r for r in range(win.model.rowCount()) if win.item_at(r).name == "DSC02997"][0]).text(), "imported item is marked with a check")

# delete selected
win.select_where(lambda it: it.name == "DSC00001")
n = len(QMessageBox.log); win.do_delete(); log = wait_dialog(n + 1)   # question box + result box
check(not (card / "DCIM/100MSDCF/DSC00001.ARW").exists(), "delete removed the file")
check((card / "AVF_INFO").exists(), "partial delete keeps the database (needs Recover Image DB)")
check(any("Recover Image DB" in m[2] for m in QMessageBox.log[n:]), "user is told to run Recover Image DB")

# offload: keep the real ARW, discard the rest, reset the database
win.select_where(lambda it: it.name == "DSC02997")
shutil.rmtree(tmp / "dest")
n = len(QMessageBox.log); win.do_offload(); log = wait_dialog(n)
check(not (card / "AVF_INFO").exists(), "offload emptied the card and removed AVF_INFO")
left = [p.name for p in card.rglob("*") if p.is_file() and "SETTING" not in str(p)]
left.sort(); check(left == ["MEDIAPRO.XML", "STATUS.BIN"], f"only camera housekeeping files remain ({left})")
check(any("DSC02997.ARW" == p.name for p in (tmp / "dest").rglob("*")), "the kept photo is in the destination")
check((card / "PRIVATE/SONY/SETTING/E10/CAMPR01P.DAT").exists(), "presets untouched")
check(any("fresh one" in m[2] for m in QMessageBox.log[n:]), "user told the camera will rebuild its database")
check(win.model.rowCount() == 0, "grid is empty after the reload")

# offload with nothing selected must refuse
win.view.clearSelection(); n = len(QMessageBox.log)
make_card(card, ["DSC00009"], [])
win.reload(); win.view.clearSelection(); win.do_offload()
check(QMessageBox.log[-1][0] == "warning" and "Nothing selected" in QMessageBox.log[-1][1], "offload with no selection is refused")
check((card / "DCIM/100MSDCF/DSC00009.ARW").exists(), "...and nothing was deleted")

# ---------------- v0.3 features
print("--- video, LUT, slow motion, full screen, frame pipeline")
from PIL import Image
make_fake_arw(card / "DCIM/100MSDCF/DSC02997.ARW"); win.reload()
prow = next(r for r in range(win.model.rowCount()) if win.item_at(r).name == "DSC02997")
pv = win.preview
pv._ensure_player()
# slow motion: offered only for 50/60/100/120 fps clips, mutes audio
pv.stack.i = 2
for fps, shown, rate in ((120.0, True, 0.25), (60.0, True, 0.5), (50.0, True, 0.5), (25.0, False, 1.0), (24.0, False, 1.0), (30.0, False, 1.0)):
    pv._fps = fps; pv.conform_box.setChecked(True); pv._apply_conform()
    check(pv.tools.isVisible() == shown and pv.player.rate == rate, f"{fps:g} fps: slow-motion box {'shown' if shown else 'hidden'}, rate {rate}")
pv._fps = 120.0; pv._apply_conform(); check(pv.audio.muted, "slow motion mutes the audio")
pv._fps = 25.0; pv._apply_conform(); check(not pv.audio.muted, "...and normal speed unmutes")
pv.conform_box.setChecked(False)
# LUT row: 'Use LUT' only exists when a LUT is loaded; name = FILE name
from zvcard import luts
cube = tmp / "Nice_Look.cube"
cube.write_text('TITLE "Some Internal Title"\nLUT_3D_SIZE 2\n' + "\n".join(f"{1-r} {1-g} {1-b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)))
check(pv.use_lut.isVisible() is False, "no LUT -> 'Use LUT' hidden")
lut = luts.parse_cube(cube)
class StubDlg:
    def __init__(self, parent, recents, folders): self.extra_folders = ["X"]; self.chosen = cube; self.clear = False
    def exec(self): return 1
main_window.LutDialog = StubDlg
# a photo is on screen: choosing a LUT re-renders it with the LUT (photos too)
win.view._cur = prow; win._load_preview(); before = list(pv.hist.data["luma"])
win.choose_lut()
check(pv.lut_label.t == "Nice_Look", f"LUT label shows the file name ({pv.lut_label.t!r})")
check(pv.use_lut.isVisible() is True, "LUT loaded -> 'Use LUT' visible")
check(list(pv.hist.data["luma"]) != before and "with LUT" in pv.hist_caption.t, "photo preview is graded with the LUT (histogram changed)")
pv.use_lut.setChecked(False); pv._lut_toggled(False)
check(list(pv.hist.data["luma"]) == before, "Use LUT off -> photo back to original")
win._fullscreen_toggle("lut"); check(pv.use_lut.isChecked() and pv._lut_active, "'L' hotkey toggles the LUT in the main window")
check(cube.exists() and "recent_luts" in QSettings.store and QSettings.store["last_lut"] == str(cube), "LUT remembered, file untouched")
class BadDlg(StubDlg):
    def __init__(self, *a): super().__init__(*a); self.chosen = tmp / "nope.cube"
main_window.LutDialog = BadDlg; n = len(QMessageBox.log); win.choose_lut()
check(QMessageBox.log[-1][0] == "warning" and win.lut is not None, "unreadable LUT -> clear message, previous LUT kept")
# video LUT routing + hotkeys S / seek
pv.stack.i = 2; pv._fps = 100.0; pv._apply_conform()
win._fullscreen_toggle("slow"); check(pv.conform_box.isChecked() and pv.player.rate == 0.25, "'S' hotkey toggles slow motion on a 100 fps clip")
win._fullscreen_toggle("slow"); pv._duration = 60000; pv.player.pos = 10000; pv.seek_by(5000); check(pv.player.pos == 15000, "Shift+Right seeks +5 s")
pv.seek_by(-99999); check(pv.player.pos == 0, "seeking never goes below 0")
pv.use_lut.setChecked(True); pv._lut_toggled(True)
check(pv.tap.show_processed and pv.player.output is pv.tap.sink and pv.surface.i == 1, "video: active LUT -> frames shown through our LUT display")
pv.use_lut.setChecked(False); pv._lut_toggled(False)
check(not pv.tap.show_processed and pv.player.output is pv.video and pv.surface.i == 0, "video: 'Use LUT' off -> original GPU widget")
pv.set_lut(None); check(pv.use_lut.isVisible() is False and pv.lut_label.t == "", "removing the LUT hides the switch again")
pv.use_lut.setChecked(True); pv.set_lut(lut, lut.name, str(cube))
# ---- frame pipeline (histogram + LUT grading)
tap = pv.tap; tap.timer.stop(); tap.synchronous = True
hists, imgs = [], []
tap.hist_ready.connect(hists.append); tap.image_ready.connect(imgs.append)
tap._prepare = lambda frame, need_img: {"pil": Image.new("RGB", (32, 18), (frame.k, 80, 120)), "tw": 32, "th": 18, "prep_ms": 1.0}
class F:
    def __init__(self, k): self.k = k
tap.reset(); tap.show_processed = False; tap.lut = None; tap._last_hist = 0.0
tap.on_frame(F(10)); check(len(hists) == 1, "first frame -> histogram (event driven, no timer needed)")
tap.on_frame(F(20)); check(len(hists) == 1, "frames in quick succession are throttled")
tap.idle_tick(); check(len(hists) == 2 and hists[-1]["r"][20] > 0, "...but once frames stop the LAST frame always gets its histogram")
tap.idle_tick(); check(len(hists) == 2, "nothing is recomputed when nothing changed")
tap.invalidate(); tap.idle_tick(); check(len(hists) == 3, "changing a setting refreshes the histogram even if no new frame arrives")
tap.show_processed = True; tap.lut = lut; tap.invalidate(); tap.idle_tick()
check(len(imgs) == 1 and len(hists) == 4, "LUT mode: picture and histogram refreshed together")
check(sum(hists[-1]["r"][233:238]) > 0 and hists[-1]["r"][20] == 0, "...and the histogram is of the GRADED picture (red 20 -> ~235)")
tap.on_frame(F(30)); check(len(imgs) == 2, "LUT mode: every new frame is graded and shown")
tap.idle_tick(); check(len(hists) == 5, "...and the settled frame gets its histogram")
# worker busy: the newest frame is kept, never lost, and shown as soon as the worker is free
tap.synchronous = True; tap._running = True; tap._job = {"waiting": True}
n_imgs = len(imgs); tap.on_frame(F(40))
check(len(imgs) == n_imgs and tap.latest is not None and tap.latest.k == 40, "busy worker: newest frame is kept for later")
tap._running = False; tap._job = None; tap._process()
check(len(imgs) == n_imgs + 1 and tap.latest is None, "...and processed when the worker is free")
# automatic quality: steps down when frames are slow, back up when there is headroom
tap.level = 0; tap._over = tap._under = 0
for _ in range(8): tap._adapt(60.0, 900)
check(tap.level == 1, "slow frames -> processing size steps down")
for _ in range(60): tap._adapt(5.0, 900)
check(tap.level == 0, "plenty of headroom -> size steps back up")
for _ in range(100): tap._adapt(60.0, 900)
check(tap.level == len(tap.LEVELS) - 1, "quality never goes below the lowest step")
tap.level = 0
check(pv._display_size()[0] >= 64, "display-size hint is always usable")
# timeline click maps to the handle CENTRE (no 1 px drift) and clamps
check(widgets.slider_value(8, 0, 100, 16, 0, 1000) == 0 and widgets.slider_value(92, 0, 100, 16, 0, 1000) == 1000, "slider: ends map to 0 and max")
check(widgets.slider_value(50, 0, 100, 16, 0, 1000) == 500 and widgets.slider_value(-50, 0, 100, 16, 0, 1000) == 0 and widgets.slider_value(999, 0, 100, 16, 0, 1000) == 1000, "slider: middle maps to middle, outside clamps")

# full screen: stage moves out, comes back; steps between items
ok = [r for r in range(win.model.rowCount())]
make_card(card, ["DSC00021", "DSC00022"], []); win.reload()
win.view._cur = 0; pv.stack.i = 1
win.toggle_fullscreen(); check(win.fs is not None and pv.stack not in pv.lay.widgets, "full screen takes the preview stage")
win.step_item(1); check(getattr(win.view.selectionModel(), "cur", None) == 1, "step_item moves to the next item")
win.toggle_fullscreen(); check(win.fs is None and pv.stack in pv.lay.widgets, "leaving full screen puts the stage back")
pv.stack.i = 0; win.toggle_fullscreen(); check(win.fs is None, "full screen needs something displayed")

# unplugging the camera: no error box, falls back to 'Choose a folder...'
shutil.rmtree(card)
n = len(QMessageBox.log); win.reload()
check(len(QMessageBox.log) == n, "disconnected card: no popup")
check("No camera card" in win.statusBar().msg, "status explains what happened")
check(win.card_box.currentData() in (None, "__choose__") or not Path(win.card_box.currentData()).exists(), "combo no longer points at the vanished drive")
# 'Clean leftovers' appears only when there are leftovers
card2 = make_card(tmp / "card2", [], ["C1001"]); QSettings.store["last_folder"] = str(card2)
win.fill_card_box(); win.reload(); check(win.b_clean.visible is False, "no leftovers -> Clean button hidden")
(card2 / "PRIVATE/M4ROOT/CLIP/C1001.MP4").unlink(); win.reload(); check(win.b_clean.visible is True, "leftovers -> Clean button shown")
# ---- real threads: fast producer (200 fps), slow worker (20 ms/frame): never deadlocks, keeps only the newest
import threading
tap2 = pipeline.FrameTap(lambda: (700, 400)); tap2.show_processed = True; tap2.lut = None; tap2.synchronous = False
gui_lock = threading.RLock()                                  # in real Qt, wake/result both run in the GUI thread
done_k, shown = [], []
tap2._prepare = lambda frame, need_img: {"k": frame.k, "pil": None, "tw": 32, "th": 18, "prep_ms": 1.0}
def slow_run(job):
    time.sleep(0.02); return {"img": (2, 2, b"x" * 12), "hist": None, "prep_ms": 1.0, "work_ms": 20.0, "k": job["k"]}
tap2._run = slow_run
orig_process, orig_result = tap2._process, tap2._on_result
tap2._process = lambda: (gui_lock.acquire(), orig_process(), gui_lock.release())
def on_result(res):
    with gui_lock:
        done_k.append(res["k"]); orig_result(res)
tap2.result.handlers = [on_result]
tap2.image_ready.connect(shown.append)
def producer():
    for k in range(100):
        tap2.on_frame(F(k)); time.sleep(0.005)
t = threading.Thread(target=producer); t.start(); t.join()
time.sleep(0.4)
check(done_k and done_k[-1] == 99, f"the LAST frame is always processed (last done = {done_k[-1] if done_k else None})")
check(len(done_k) < 100 and len(done_k) > 5, f"slow worker drops old frames instead of queueing ({len(done_k)} of 100 processed)")
check(done_k == sorted(done_k) and len(set(done_k)) == len(done_k), "frames are shown in order, none twice")
check(tap2._running is False and tap2._job is None, "worker is idle afterwards (no stuck state)")
tap2.stop(); time.sleep(0.1); check(not tap2._thread.is_alive(), "stop() ends the worker thread")

print("\nFAILED:", check.failed); sys.exit(1 if check.failed else 0)
