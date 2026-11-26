import unittest

import support
from rpicrt import cmdline as c

STOCK = support.fixture("cmdline.txt.stock")
LEGACY = support.fixture("cmdline.txt.legacy")
CONN, EDID, MODE = "HDMI-A-1", "edid/converter-crt.bin", "720x480@60iD"
OURS = ["drm.edid_firmware=HDMI-A-1:edid/converter-crt.bin", "video=HDMI-A-1:720x480@60iD"]


class InstallTests(unittest.TestCase):
    def test_appends_owned_args_and_preserves_everything_else(self):
        new, rec = c.plan_install(STOCK, CONN, EDID, MODE)
        self.assertEqual(new, STOCK.rstrip("\n") + " " + " ".join(OURS) + "\n")
        self.assertEqual(rec["edid"]["prior"], None)
        self.assertEqual(rec["video"]["prior"], None)
        self.assertIn("root=PARTUUID=0badc0de-02", new)
        self.assertEqual(new.count("\n"), 1)

    def test_idempotent_on_archived_line(self):
        new, rec = c.plan_install(LEGACY, CONN, EDID, MODE)
        self.assertEqual(new, LEGACY)
        self.assertEqual(rec["edid"]["prior"], EDID)  # original baseline already had it
        new2, _ = c.plan_install(new, CONN, EDID, MODE)
        self.assertEqual(new2, new)

    def test_reinstall_keeps_original_prior(self):
        new, rec = c.plan_install("root=X video=HDMI-A-1:1280x720\n", CONN, EDID, MODE)
        again, rec2 = c.plan_install(new, CONN, EDID, MODE, previous=rec)
        self.assertEqual(again, new)
        self.assertEqual(rec2["video"]["prior"], "video=HDMI-A-1:1280x720")

    def test_replaces_existing_connector_values_in_place(self):
        text = "root=X video=HDMI-A-1:1280x720 quiet drm.edid_firmware=HDMI-A-1:edid/old.bin\n"
        new, rec = c.plan_install(text, CONN, EDID, MODE)
        self.assertEqual(new, "root=X video=HDMI-A-1:720x480@60iD quiet drm.edid_firmware=HDMI-A-1:edid/converter-crt.bin\n")
        self.assertEqual(rec["edid"]["prior"], "edid/old.bin")

    def test_merges_with_other_connector_entries(self):
        text = "root=X drm.edid_firmware=HDMI-A-2:edid/tv.bin video=HDMI-A-2:1920x1080\n"
        new, rec = c.plan_install(text, CONN, EDID, MODE)
        self.assertIn("drm.edid_firmware=HDMI-A-2:edid/tv.bin,HDMI-A-1:edid/converter-crt.bin", new)
        self.assertIn("video=HDMI-A-2:1920x1080", new)
        restored, conflicts = c.plan_uninstall(new, rec)
        self.assertEqual((restored, conflicts), (text, []))

    def test_conflicts_are_refused(self):
        for text in (
            "root=X drm.edid_firmware=edid/global.bin\n",              # global EDID
            "root=X drm.edid_firmware=HDMI-A-1:a.bin drm.edid_firmware=HDMI-A-1:b.bin\n",
            "root=X video=HDMI-A-1:a video=HDMI-A-1:b\n",
            "root=X video=720x480\n",                                  # global video=
            "root=X drm_kms_helper.edid_firmware=HDMI-A-1:edid/x.bin\n",
            "root=X\nquiet\n",                                         # multi-line
            "\n",
        ):
            with self.subTest(text=text), self.assertRaises(c.CmdlineError):
                c.plan_install(text, CONN, EDID, MODE)

    def test_no_trailing_newline_preserved(self):
        new, _ = c.plan_install("root=X quiet", CONN, EDID, MODE)
        self.assertFalse(new.endswith("\n"))


class UninstallTests(unittest.TestCase):
    def test_round_trip_restores_exact_bytes(self):
        new, rec = c.plan_install(STOCK, CONN, EDID, MODE)
        restored, conflicts = c.plan_uninstall(new, rec)
        self.assertEqual(conflicts, [])
        self.assertEqual(restored, STOCK)

    def test_restores_prior_values(self):
        text = "root=X video=HDMI-A-1:1280x720\n"
        new, rec = c.plan_install(text, CONN, EDID, MODE)
        restored, conflicts = c.plan_uninstall(new, rec)
        self.assertEqual((restored, conflicts), (text, []))

    def test_user_edit_after_install_is_a_conflict(self):
        new, rec = c.plan_install(STOCK, CONN, EDID, MODE)
        edited = new.replace("video=HDMI-A-1:720x480@60iD", "video=HDMI-A-1:720x576@50iD")
        restored, conflicts = c.plan_uninstall(edited, rec)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(restored, edited)  # whole line left exactly as the owner made it

    def test_already_restored_is_not_a_conflict(self):
        _, rec = c.plan_install(STOCK, CONN, EDID, MODE)
        restored, conflicts = c.plan_uninstall(STOCK, rec)
        self.assertEqual((restored, conflicts), (STOCK, []))

    def test_duplicates_counter(self):
        tokens = (STOCK.strip() + " " + " ".join(OURS)).split()
        self.assertEqual(c.duplicates(tokens, CONN), {"edid_args": 1, "video_args": 1})


if __name__ == "__main__":
    unittest.main()
