import io, os, shutil, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from zvcard import media, scan
from fixtures import clip_xml, jpeg_bytes, make_card, make_fake_arw


class TestMedia(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.card = make_card(self.tmp / "card", [], [])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _arw_item(self, name="DSC02997", **kw):
        make_fake_arw(self.card / f"DCIM/100MSDCF/{name}.ARW", **kw)
        return next(i for i in scan.scan_card(self.card).photos if i.name == name)

    def _video_item(self, **xml_kw):
        root = self.card / "PRIVATE/M4ROOT"
        (root / "CLIP/C1063.MP4").write_bytes(b"x" * 100)
        (root / "CLIP/C1063M01.XML").write_text(clip_xml(**xml_kw))
        (root / "THMBNL/C1063T01.JPG").write_bytes(jpeg_bytes((320, 180)))
        return scan.scan_card(self.card).videos[0]

    # ---- ARW
    def test_arw_preview_and_info(self):
        path = self.tmp / "a.ARW"
        make_fake_arw(path)
        ri = media.read_arw(path)
        self.assertEqual(Image.open(io.BytesIO(ri.preview)).size, (1616, 1080))
        self.assertEqual(Image.open(io.BytesIO(ri.thumb)).size, (160, 120))
        self.assertEqual((ri.width, ri.height, ri.orientation), (6048, 4024, 1))
        self.assertEqual((ri.exif["iso"], ri.exif["shutter"], ri.exif["aperture"]), (800, 0.005, 8.0))

    def test_arw_reads_only_what_it_needs(self):
        path = self.tmp / "a.ARW"
        info = make_fake_arw(path, raw_padding=2_000_000)             # a real ARW carries ~25 MB of RAW data
        path.write_bytes(path.read_bytes()[:info["preview_end"]])     # ...none of which is needed for the preview
        ri = media.read_arw(path)
        self.assertTrue(ri.preview)
        self.assertEqual(ri.exif["iso"], 800)

    def test_damaged_exposure_data_still_gives_the_preview(self):
        path = self.tmp / "a.ARW"
        make_fake_arw(path)
        raw = bytearray(path.read_bytes())
        pos = raw.index(b"\x9a\x82\x05\x00")             # EXIF entry: tag 0x829A, type RATIONAL
        raw[pos + 8:pos + 12] = (10_000_000).to_bytes(4, "little")   # its value now points outside the file
        path.write_bytes(bytes(raw))
        ri = media.read_arw(path)
        self.assertTrue(ri.preview)                                   # the picture is still shown...
        self.assertEqual(ri.exif, {})                                 # ...only the exposure line is missing

    def test_file_without_any_preview_gives_a_clean_error(self):
        path = self.tmp / "a.ARW"
        path.write_bytes(b"II*\0\x08\0\0\0" + b"\0\0" + b"\0\0\0\0")   # valid TIFF header, empty IFD
        with self.assertRaises(ValueError):
            media.read_arw(path)

    def test_arw_cache_is_used_and_invalidated_when_the_file_changes(self):
        path = self.tmp / "a.ARW"
        make_fake_arw(path, iso=100)
        first = media.read_arw(path)
        self.assertIs(media.read_arw(path), first)                   # served from the cache
        make_fake_arw(path, iso=6400)                                # file changed (size/mtime differ)
        os.utime(path, (1, 1))
        self.assertEqual(media.read_arw(path).exif["iso"], 6400)

    def test_orientation_applied_to_raw_preview(self):
        im = Image.new("RGB", (40, 20))
        self.assertEqual(media._apply_orientation(im, 6).size, (20, 40))
        self.assertEqual(media._apply_orientation(im, 1).size, (40, 20))
        it = self._arw_item("DSC00007", orientation=6, preview=(160, 90))
        self.assertEqual(media.load_photo(it, 1000).size, (90, 160))

    # ---- photos
    def test_photo_display_thumb_histogram(self):
        it = self._arw_item()
        img = media.load_photo(it, 1200)
        self.assertEqual(img.mode, "RGB")
        self.assertLessEqual(max(img.size), 1200)
        self.assertLessEqual(max(media.make_thumbnail(it, 200).size), 200)
        h = media.histogram(img)
        self.assertEqual(set(h), {"r", "g", "b", "luma"})
        self.assertEqual(len(h["luma"]), 256)
        self.assertEqual(sum(h["luma"]), sum(h["r"]))

    def test_jpeg_preferred_when_pair_and_orientation(self):
        self._arw_item("DSC00005")
        ex = Image.Exif()
        ex[0x0112] = 6
        Image.new("RGB", (300, 200), (200, 10, 10)).save(self.card / "DCIM/100MSDCF/DSC00005.JPG", exif=ex)
        it = next(i for i in scan.scan_card(self.card).photos if i.name == "DSC00005")
        img = media.load_photo(it, 1000)
        self.assertEqual(img.size, (200, 300))                       # rotated by the EXIF flag
        self.assertGreater(img.getpixel((10, 10))[0], 150)           # came from the red JPEG, not the RAW

    def test_describe_photo(self):
        rows = dict(media.describe(self._arw_item()))
        self.assertEqual(rows["Resolution"], "6048 x 4024")
        self.assertEqual(rows["Exposure"], "1/200 s  f/8.0  ISO 800  77 mm")

    def test_corrupt_files_do_not_crash(self):
        (self.card / "DCIM/100MSDCF/DSC09999.ARW").write_bytes(os.urandom(2000))
        it = next(i for i in scan.scan_card(self.card).photos if i.name == "DSC09999")
        self.assertIsNone(media.make_thumbnail(it, 100))
        self.assertTrue(media.describe(it))                          # still returns the basic rows
        with self.assertRaises(Exception):
            media.load_photo(it)

    # ---- video
    def test_video_thumbnail_and_metadata(self):
        it = self._video_item()
        self.assertIsNotNone(media.make_thumbnail(it, 256))
        m = media.clip_metadata(it)
        self.assertEqual((m["width"], m["height"], m["fps"], m["frames"]), ("3840", "2160", "25p", 336))
        self.assertEqual(m["gamma"], "ex-cine2")
        rows = dict(media.describe(it))
        self.assertEqual((rows["Resolution"], rows["Length"]), ("3840 x 2160", "13.4 s"))
        self.assertFalse(media.is_log_like(m))
        self.assertTrue(media.is_log_like({"gamma": "s-log3-cine"}))

    def test_frame_rate_helpers(self):
        self.assertEqual([media.conform_ratio(x) for x in (None, 24, 25, 30, 50, 59.94, 60, 100, 119.88, 120)],
                         [None, None, None, None, 0.5, 0.5, 0.5, 0.25, 0.25, 0.25])
        self.assertEqual(media.fps_value("59.94p"), 59.94)
        self.assertIsNone(media.fps_value(None))
        m = media.clip_metadata(self._video_item(fps="120p"))
        self.assertEqual(media.fps_value(m["format_fps"]), 120.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
