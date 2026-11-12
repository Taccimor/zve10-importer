import os, shutil, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from zvcard import luts

def cube3d(path, fn, n=9, title="t"):
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}"]
    for b in range(n):
        for g in range(n):
            for r in range(n):
                lines.append(" ".join(f"{v:.6f}" for v in fn(r / (n - 1), g / (n - 1), b / (n - 1))))
    Path(path).write_text("\n".join(lines))

class TestLut(unittest.TestCase):
    def setUp(self): self.tmp = Path(tempfile.mkdtemp())
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)
    def img(self):
        im = Image.new("RGB", (16, 16))
        im.putdata([(x * 16, y * 16, (x + y) * 8) for y in range(16) for x in range(16)]); return im
    def test_identity_is_identity(self):
        cube3d(self.tmp / "i.cube", lambda r, g, b: (r, g, b))
        out = luts.apply_lut(self.img(), luts.parse_cube(self.tmp / "i.cube"))
        self.assertEqual(list(out.getdata()), list(self.img().getdata()))
    def test_invert_and_channel_order(self):
        cube3d(self.tmp / "inv.cube", lambda r, g, b: (1 - r, g, b))        # only red inverted
        out = luts.apply_lut(self.img(), luts.parse_cube(self.tmp / "inv.cube"))
        src = self.img()
        for pt in [(0, 0), (5, 3), (15, 15)]:
            a, b = src.getpixel(pt), out.getpixel(pt)
            self.assertLessEqual(abs(b[0] - (255 - a[0])), 1)
            self.assertEqual(b[1:], a[1:])
    def test_swap_channels_checks_cube_ordering(self):
        cube3d(self.tmp / "s.cube", lambda r, g, b: (b, g, r))              # swap R and B
        out = luts.apply_lut(Image.new("RGB", (2, 2), (200, 100, 20)), luts.parse_cube(self.tmp / "s.cube"))
        r, g, b = out.getpixel((0, 0))
        self.assertTrue(abs(r - 20) <= 2 and abs(g - 100) <= 2 and abs(b - 200) <= 2, (r, g, b))
    def test_1d_lut(self):
        (self.tmp / "g.cube").write_text('TITLE "gamma"\nLUT_1D_SIZE 5\n' + "\n".join(
            f"{(i / 4) ** 2:.6f} {(i / 4) ** 2:.6f} {(i / 4) ** 2:.6f}" for i in range(5)))
        lut = luts.parse_cube(self.tmp / "g.cube"); self.assertEqual(lut.kind, "1d")
        out = luts.apply_lut(Image.new("RGB", (1, 1), (128, 128, 128)), lut)
        self.assertTrue(abs(out.getpixel((0, 0))[0] - 64) <= 6)
    def test_comments_title_and_domain(self):
        (self.tmp / "c.cube").write_text('# comment\nTITLE "My Look"\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n'
                                         'LUT_3D_SIZE 2\n' + "\n".join(
            f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)))
        lut = luts.parse_cube(self.tmp / "c.cube")
        self.assertEqual((lut.title, lut.name, lut.size), ("My Look", "c", 2))   # name = FILE name
    def test_bad_files_raise_clean_errors(self):
        for name, text in {"a.cube": "hello", "b.cube": "LUT_3D_SIZE 2\n0 0 0\n", "c.cube": "LUT_3D_SIZE 2\n" + "x y z\n" * 8}.items():
            (self.tmp / name).write_text(text)
            with self.assertRaises((luts.LutError, ValueError)): luts.parse_cube(self.tmp / name)
        with self.assertRaises(luts.LutError): luts.parse_cube(self.tmp / "missing.cube")
    def test_find_luts_in_known_and_extra_folders_without_touching_them(self):
        pd = self.tmp / "pd"; res = pd / "Blackmagic Design/DaVinci Resolve/Support/LUT/Sony"; res.mkdir(parents=True)
        cube3d(res / "a.cube", lambda r, g, b: (r, g, b)); (res / "notes.txt").write_text("x")
        mine = self.tmp / "mine/deep"; mine.mkdir(parents=True); cube3d(mine / "b.CUBE", lambda r, g, b: (r, g, b))
        old = {k: os.environ.get(k) for k in ("PROGRAMDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")}
        os.environ.update(PROGRAMDATA=str(pd), APPDATA=str(self.tmp / "nope"), PROGRAMFILES=str(self.tmp / "nope"))
        os.environ["PROGRAMFILES(X86)"] = str(self.tmp / "nope")
        try:
            before = sorted(str(p) for p in self.tmp.rglob("*"))
            found = luts.find_luts([str(self.tmp / "mine")])
            self.assertEqual(sorted(str(p) for p in self.tmp.rglob("*")), before)     # nothing moved/created
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(sorted((l, p.name) for l, r, p in found), [("DaVinci Resolve", "a.cube"), ("Your folders", "b.CUBE")])
        by = {p.name: (r, p) for l, r, p in found}                  # roots allow rebuilding the folder tree
        self.assertEqual(by["a.cube"][1].relative_to(by["a.cube"][0]).parts, ("Sony", "a.cube"))
        self.assertEqual(by["b.CUBE"][1].relative_to(by["b.CUBE"][0]).parts, ("deep", "b.CUBE"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
