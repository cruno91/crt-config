import unittest

import support
from rpicrt import configtxt as ct

STOCK = support.fixture("config.txt.stock")
LEGACY = support.fixture("config.txt.legacy")
PI5, OTHER = support.MODEL, support.UNTESTED_MODEL


class Vc4OverlayTests(unittest.TestCase):
    def test_finds_stock_overlay(self):
        info = ct.find_vc4_overlay(STOCK, PI5)
        self.assertEqual(info["line"], "dtoverlay=vc4-kms-v3d")
        self.assertFalse(info["noaudio"])
        self.assertTrue(ct.find_vc4_overlay(LEGACY, PI5)["noaudio"])

    def test_model_sections(self):
        text = "[pi3]\ndtoverlay=vc4-kms-v3d,cma-512\n[pi5]\ndtoverlay=vc4-kms-v3d-pi5\n[all]\n"
        self.assertEqual(ct.find_vc4_overlay(text, PI5)["overlay"], "vc4-kms-v3d-pi5")
        self.assertEqual(ct.find_vc4_overlay(text, OTHER)["params"], ["cma-512"])
        # compute-module and Pi 500 sections do not apply to a Pi 5 Model B
        text = "[cm5]\ndtoverlay=vc4-kms-v3d,cma-512\n[pi500]\ndtoverlay=vc4-kms-v3d,cma-384\n[all]\ndtoverlay=vc4-kms-v3d\n"
        self.assertEqual(ct.find_vc4_overlay(text, PI5)["params"], [])
        with self.assertRaises(ct.ConfigTxtError):
            ct.find_vc4_overlay("[none]\ndtoverlay=vc4-kms-v3d\n", PI5)

    def test_model_filter_derivation(self):
        self.assertEqual(ct.model_filters("Raspberry Pi 5 Model B Rev 1.0"), {"pi5"})
        self.assertEqual(ct.model_filters("Raspberry Pi 500 Rev 1.0"), {"pi5", "pi500"})
        self.assertEqual(ct.model_filters("Raspberry Pi Compute Module 5 Rev 1.0"), {"pi5", "cm5"})
        self.assertEqual(ct.model_filters("Raspberry Pi 3 Model B Plus Rev 1.3"), {"pi3", "pi3+"})
        self.assertEqual(ct.model_filters("Raspberry Pi Zero 2 W Rev 1.0"), {"pi0", "pi02"})
        self.assertIsNone(ct.model_filter_applies("HDMI:0", "Raspberry Pi 5 Model B Rev 1.0"))

    def test_ambiguous_or_unknown_filters_are_refused(self):
        for text in ("dtoverlay=vc4-kms-v3d\ndtoverlay=vc4-kms-v3d\n",
                     "[HDMI:0]\ndtoverlay=vc4-kms-v3d\n",
                     "#dtoverlay=vc4-kms-v3d\n",
                     "dtoverlay=vc4-kms-v3d,noaudio=off\n"):
            with self.subTest(text=text), self.assertRaises(ct.ConfigTxtError):
                ct.find_vc4_overlay(text, PI5)

    def test_dtparam_form_counts_as_disabled(self):
        info = ct.find_vc4_overlay("dtoverlay=vc4-kms-v3d\ndtparam=noaudio\n", PI5)
        self.assertTrue(info["noaudio"])
        # a dtparam before the overlay applies to the base DT, not the overlay
        self.assertFalse(ct.find_vc4_overlay("dtparam=noaudio\ndtoverlay=vc4-kms-v3d\n", PI5)["noaudio"])


class AudioPolicyTests(unittest.TestCase):
    def test_disabled_adds_noaudio_and_round_trips(self):
        new, rec = ct.plan_audio(STOCK, PI5, "disabled")
        self.assertEqual(new, LEGACY)  # exactly the archived working line
        self.assertEqual(rec, {"installed_line": "dtoverlay=vc4-kms-v3d,noaudio", "prior_line": "dtoverlay=vc4-kms-v3d"})
        restored, conflicts = ct.plan_uninstall(new, rec)
        self.assertEqual((restored, conflicts), (STOCK, []))

    def test_already_disabled_is_not_owned(self):
        new, rec = ct.plan_audio(LEGACY, PI5, "disabled")
        self.assertEqual((new, rec), (LEGACY, None))

    def test_reinstall_keeps_record(self):
        new, rec = ct.plan_audio(STOCK, PI5, "disabled")
        again, rec2 = ct.plan_audio(new, PI5, "disabled", previous=rec)
        self.assertEqual((again, rec2), (new, rec))

    def test_preserve_changes_nothing(self):
        self.assertEqual(ct.plan_audio(STOCK, PI5, "preserve"), (STOCK, None))

    def test_preserves_other_params(self):
        new, rec = ct.plan_audio("dtoverlay=vc4-kms-v3d,cma-384\n", PI5, "disabled")
        self.assertEqual(new, "dtoverlay=vc4-kms-v3d,cma-384,noaudio\n")

    def test_uninstall_conflict_when_line_changed(self):
        new, rec = ct.plan_audio(STOCK, PI5, "disabled")
        edited = new.replace("vc4-kms-v3d,noaudio", "vc4-kms-v3d,noaudio,cma-512")
        restored, conflicts = ct.plan_uninstall(edited, rec)
        self.assertEqual(restored, edited)
        self.assertEqual(len(conflicts), 1)


class MiscTests(unittest.TestCase):
    def test_overlay_readme_detection(self):
        self.assertTrue(ct.overlay_documents_noaudio(support.fixture("overlays-README.excerpt")))
        self.assertTrue(ct.overlay_documents_noaudio("Name:   vc4-kms-v3d\nParams: noaudio   Disable\n"))
        self.assertFalse(ct.overlay_documents_noaudio("Name:   vc4-kms-v3d\nParams: audio  x\n\nName:   other\nParams: noaudio\n"))
        self.assertFalse(ct.overlay_documents_noaudio(""))

    def test_keys_and_initramfs(self):
        self.assertEqual(ct.find_key(STOCK, "disable_fw_kms_setup", PI5), ["1"])
        self.assertEqual(ct.find_key(STOCK, "otg_mode", PI5), [])  # in a compute-module-only section
        self.assertIsNone(ct.explicit_initramfs(STOCK, PI5))
        self.assertEqual(ct.explicit_initramfs("initramfs initrd.img followkernel\n", PI5), "initrd.img")
        self.assertEqual(ct.legacy_video_settings("hdmi_group=1\nhdmi_mode=6\n", PI5), ["hdmi_group=1", "hdmi_mode=6"])


if __name__ == "__main__":
    unittest.main()
