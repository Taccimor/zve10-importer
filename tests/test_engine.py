import os, shutil, stat, sys, tempfile, unittest, zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zvcard import mediapro, ops, scan, workflow
from zvcard.history import History
from fixtures import catalog, make_card

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.card = self.tmp / "card"; self.dest = self.tmp / "dest"; self.bk = self.tmp / "bk"
        self.dest.mkdir()
    def tearDown(self):
        for dp, dn, fn in os.walk(self.tmp):
            for f in fn: os.chmod(Path(dp) / f, 0o666)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def files(self, sub):  # sorted list of file names below card/sub
        return sorted(p.name for p in (self.card / sub).rglob("*") if p.is_file())

class TestMediapro(Base):
    def test_remove_one_keeps_others_and_crlf(self):
        new, removed = mediapro.remove_materials(catalog(["C1072", "C1073"]), {"C1072"})
        self.assertEqual(removed, ["C1072"])
        self.assertEqual(new, catalog(["C1073"]))
        self.assertNotIn("\n\n", new.replace("\r\n", "\n"))
    def test_remove_all_gives_formatted_card_layout(self):
        new, _ = mediapro.remove_materials(catalog(["C1072", "C1073"]), {"C1072", "C1073"})
        self.assertEqual(new, catalog([]))            # identical to a clean card's file
    def test_unknown_clip_not_touched(self):
        t = catalog(["C1072"]); new, removed = mediapro.remove_materials(t, {"C9999"})
        self.assertEqual((new, removed), (t, []))
    def test_lf_line_endings_and_system_kind(self):
        t = catalog(["C1072", "C1073"]).replace("\r\n", "\n")
        new, _ = mediapro.remove_materials(t, {"C1073"})
        self.assertEqual(new, catalog(["C1072"]).replace("\r\n", "\n"))
        self.assertEqual(mediapro.system_kind(t), "ZV-E10")
    def test_camera_padded_file(self):
        t = catalog(["C1072"]) + " " * 300
        new, _ = mediapro.remove_materials(t, {"C1072"})
        self.assertTrue(new.startswith(catalog([]).rstrip()))

class TestScan(Base):
    def test_groups_pairs_videos_orphans_stale(self):
        make_card(self.card, ["DSC00001", "DSC00002"], ["C1001", "C1002"], raw_pairs=True, proxies=True)
        (self.card / "PRIVATE/M4ROOT/CLIP/C1002.MP4").unlink()      # "cut in Explorer"
        (self.card / "PRIVATE/M4ROOT/CLIP/C1002M01.XML").unlink()
        info = scan.scan_card(self.card)
        self.assertEqual([i.name for i in info.photos], ["DSC00001", "DSC00002"])
        self.assertEqual(len(info.photos[0].files), 2)
        self.assertEqual([i.name for i in info.videos], ["C1001"])
        self.assertEqual({f.role for f in info.videos[0].files}, {"clip", "clip_xml", "thumb", "proxy"})
        self.assertEqual({f.path.name for f in info.orphans}, {"C1002T01.JPG", "C1002S03.MP4"})
        self.assertEqual(info.stale_clips, ["C1002"])
    def test_rejects_other_models_and_non_cards(self):
        make_card(self.card, [], ["C1001"])
        p = self.card / "PRIVATE/M4ROOT/MEDIAPRO.XML"
        p.write_bytes(p.read_bytes().replace(b"ZV-E10", b"ILCE-7M4"))
        with self.assertRaises(scan.NotACard): scan.scan_card(self.card)
        with self.assertRaises(scan.NotACard): scan.scan_card(self.tmp)
    def test_foreign_and_unknown_detected(self):
        make_card(self.card, ["DSC00001"], [])
        (self.card / "MYVIDEOS").mkdir(); (self.card / "MYVIDEOS/a.mp4").write_bytes(b"x")
        (self.card / "DCIM/100MSDCF/notes.txt").write_text("hi")
        info = scan.scan_card(self.card)
        self.assertEqual(len(info.foreign), 1); self.assertEqual(len(info.unknown), 1)

class TestNoFalseForeign(Base):
    def test_normal_card_has_no_foreign_or_unknown(self):
        make_card(self.card, ["DSC00001"], ["C1001"], raw_pairs=True, proxies=True)
        info = scan.scan_card(self.card)
        self.assertEqual((info.foreign, info.unknown), ([], []))

class TestImportDelete(Base):
    def test_import_verifies_and_layouts(self):
        make_card(self.card, ["DSC00001"], ["C1001"])
        info = scan.scan_card(self.card)
        res = ops.import_items(info.items, self.dest)
        self.assertTrue(all(r.status == "ok" for r in res))
        names = sorted(p.name for p in self.dest.rglob("*") if p.is_file())
        self.assertEqual(names, ["C1001.MP4", "C1001M01.XML", "DSC00001.ARW"])   # thumb not copied
        self.assertTrue(any("Videos" in str(p) for p in self.dest.rglob("C1001.MP4")))
        self.assertEqual(self.files("DCIM"), ["DSC00001.ARW"])                   # card untouched
        self.assertFalse(list(self.dest.rglob("*.part")))
    def test_collision_rename_and_identical_skip(self):
        make_card(self.card, ["DSC00001"], [])
        info = scan.scan_card(self.card)
        ops.import_items(info.items, self.dest)
        r2 = ops.import_items(info.items, self.dest)                  # same file again
        self.assertEqual(r2[0].status, "skipped_identical")
        f = self.card / "DCIM/100MSDCF/DSC00001.ARW"
        old = f.stat().st_mtime
        f.write_bytes(b"different content"); os.utime(f, (old, old))   # same day, new content
        info = scan.scan_card(self.card)
        r3 = ops.import_items(info.items, self.dest)                  # same name, new content
        self.assertEqual(r3[0].status, "ok")
        self.assertEqual(sorted(p.name for p in self.dest.rglob("*.ARW")), ["DSC00001.ARW", "DSC00001_1.ARW"])
    def test_delete_removes_all_sidecars_and_catalog_entry(self):
        make_card(self.card, ["DSC00001"], ["C1001", "C1002"], raw_pairs=True, proxies=True)
        info = scan.scan_card(self.card)
        rep = ops.delete_items(self.card, [i for i in info.items if i.name in ("C1001", "DSC00001")])
        self.assertEqual(rep.catalog_removed, ["C1001"])
        self.assertEqual(self.files("PRIVATE/M4ROOT/CLIP"), ["C1002.MP4", "C1002M01.XML"])
        self.assertEqual(self.files("PRIVATE/M4ROOT/THMBNL"), ["C1002T01.JPG"])
        self.assertEqual(self.files("PRIVATE/M4ROOT/SUB"), ["C1002S03.MP4"])
        self.assertEqual(self.files("DCIM"), [])
        after = scan.scan_card(self.card)
        self.assertFalse(after.has_leftovers)
        self.assertEqual([i.name for i in after.items], ["C1002"])
    def test_protected_files_are_skipped(self):
        make_card(self.card, ["DSC00001", "DSC00002"], [])
        os.chmod(self.card / "DCIM/100MSDCF/DSC00002.ARW", stat.S_IREAD)
        info = scan.scan_card(self.card)
        rep = ops.delete_items(self.card, info.items)
        self.assertEqual([i.name for i in rep.skipped_protected], ["DSC00002"])
        self.assertEqual(self.files("DCIM"), ["DSC00002.ARW"])
    def test_dry_run_changes_nothing(self):
        make_card(self.card, ["DSC00001"], ["C1001"])
        before = sorted(p.relative_to(self.card).as_posix() for p in self.card.rglob("*"))
        mp = (self.card / "PRIVATE/M4ROOT/MEDIAPRO.XML").read_bytes()
        info = scan.scan_card(self.card)
        ops.delete_items(self.card, info.items, dry_run=True)
        ops.import_items(info.items, self.dest, dry_run=True)
        self.assertEqual(before, sorted(p.relative_to(self.card).as_posix() for p in self.card.rglob("*")))
        self.assertEqual(mp, (self.card / "PRIVATE/M4ROOT/MEDIAPRO.XML").read_bytes())
        self.assertEqual(list(self.dest.rglob("*")), [])
    def test_destination_folder_may_not_exist_yet(self):
        make_card(self.card, ["DSC00001"], [])
        info = scan.scan_card(self.card)
        res = ops.import_items(info.items, self.tmp / "new" / "deep" / "folder")
        self.assertEqual(res[0].status, "ok")
        self.assertEqual(len(list((self.tmp / "new").rglob("*.ARW"))), 1)

    def test_failed_copy_never_deletes_from_card(self):
        make_card(self.card, ["DSC00001"], [])
        info = scan.scan_card(self.card)
        orig = ops._copy_verified
        def boom(*a, **k): raise IOError("disk full")
        ops._copy_verified = boom
        try:
            rep = workflow.run_session(self.card, info.items, [], self.dest,
                                       ops.ImportOptions(delete_after=True), self.bk)
        finally:
            ops._copy_verified = orig
        self.assertEqual(rep.imported[0].status, "failed")
        self.assertEqual(self.files("DCIM"), ["DSC00001.ARW"])
        self.assertEqual(rep.state, "nothing_removed")

class TestCleanAndReset(Base):
    def test_clean_leftovers_after_explorer_cut(self):
        make_card(self.card, [], ["C1001", "C1002"], proxies=True)
        for n in ("C1001.MP4", "C1001M01.XML"): (self.card / "PRIVATE/M4ROOT/CLIP" / n).unlink()
        rep = ops.clean_leftovers(self.card)
        self.assertEqual(rep.catalog_removed, ["C1001"])
        self.assertEqual(self.files("PRIVATE/M4ROOT/THMBNL"), ["C1002T01.JPG"])
        self.assertEqual(self.files("PRIVATE/M4ROOT/SUB"), ["C1002S03.MP4"])
        self.assertFalse(scan.scan_card(self.card).has_leftovers)
    def test_reset_blocked_unless_empty(self):
        make_card(self.card, ["DSC00001"], [])
        with self.assertRaises(ops.ResetBlocked): ops.reset_card(self.card, self.bk)
        self.assertTrue((self.card / "AVF_INFO").exists())
    def test_full_offload_resets_db_keeps_presets(self):
        make_card(self.card, ["DSC00001", "DSC00002"], ["C1001"], raw_pairs=True, proxies=True)
        info = scan.scan_card(self.card)
        hist = History(self.tmp / "h.json")
        rep = workflow.run_session(self.card, info.items, [], self.dest,
                                   ops.ImportOptions(delete_after=True), self.bk, history=hist)
        self.assertEqual(rep.state, "reset_done")
        self.assertFalse((self.card / "AVF_INFO").exists())
        self.assertEqual(self.files("DCIM"), []); self.assertEqual(self.files("PRIVATE/M4ROOT/CLIP"), [])
        self.assertEqual(self.files("PRIVATE/M4ROOT/THMBNL"), [])
        self.assertEqual((self.card / "PRIVATE/M4ROOT/MEDIAPRO.XML").read_bytes().decode(), catalog([]))
        self.assertTrue((self.card / "PRIVATE/SONY/SETTING/E10/CAMPR01P.DAT").exists())
        with zipfile.ZipFile(rep.backup) as z:                         # backup holds DB + presets
            names = z.namelist()
        self.assertTrue(any("AVIN0001.INP" in n for n in names) and any("CAMPR01P" in n for n in names))
        self.assertEqual(len(list(self.dest.rglob("*.ARW"))), 2)
        self.assertTrue(all(hist.is_imported(i) for i in info.items))
    def test_reset_works_when_database_is_read_only(self):
        make_card(self.card, [], [])
        avf = self.card / "AVF_INFO"
        for f in avf.iterdir(): os.chmod(f, stat.S_IREAD)       # like the camera's flags
        os.chmod(avf, stat.S_IREAD | stat.S_IEXEC)
        ops.reset_card(self.card, self.bk)
        self.assertFalse(avf.exists())
    def test_reset_failure_is_reported_not_raised(self):
        make_card(self.card, ["DSC00001"], [])
        info = scan.scan_card(self.card)
        orig = ops.remove_tree
        def boom(p): raise PermissionError(5, "Access is denied")
        ops.remove_tree = boom
        try:
            rep = workflow.run_session(self.card, [], info.items, None, ops.ImportOptions(), self.bk)
        finally:
            ops.remove_tree = orig
        self.assertEqual(rep.state, "reset_failed")
        self.assertTrue(any("AVF_INFO" in m for m in rep.messages))
        self.assertEqual(self.files("DCIM"), [])
    def test_partial_discard_asks_for_db_recovery_and_keeps_db(self):
        make_card(self.card, ["DSC00001", "DSC00002"], ["C1001"])
        info = scan.scan_card(self.card)
        rep = workflow.run_session(self.card, [], [info.items[0]], None, ops.ImportOptions(), self.bk)
        self.assertEqual(rep.state, "needs_recovery")
        self.assertTrue((self.card / "AVF_INFO").exists())
    def test_empty_but_unknown_files_blocks_reset(self):
        make_card(self.card, ["DSC00001"], [])
        (self.card / "OTHERCAM").mkdir(); (self.card / "OTHERCAM/clip.mp4").write_bytes(b"x")
        info = scan.scan_card(self.card)
        rep = workflow.run_session(self.card, [], info.items, None, ops.ImportOptions(), self.bk)
        self.assertEqual(rep.state, "needs_recovery")
        self.assertTrue((self.card / "AVF_INFO").exists())
        self.assertTrue(any("could not be reset" in m for m in rep.messages))

if __name__ == "__main__":
    unittest.main(verbosity=2)
