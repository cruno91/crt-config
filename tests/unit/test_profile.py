import os
import unittest

import support  # noqa: F401  (sets sys.path)
from rpicrt import profile

GOOD = support.read(support.REPO, "/profiles/sony-trinitron-lontium.conf")


class ProfileTests(unittest.TestCase):
    def test_shipped_profile_matches_archived_values(self):
        p = profile.load(support.REPO, "sony-trinitron-lontium")
        self.assertEqual(p.mode_name, "720x480i-crt")
        self.assertEqual(p.clock_khz, 13500)
        self.assertEqual(p.h, (720, 779, 841, 858))
        self.assertEqual(p.v, (480, 488, 494, 525))
        self.assertEqual(p.mode_flag_bits, 0x101A)
        self.assertEqual(p.margins, {"left": 8, "right": 48, "top": 16, "bottom": 16})
        self.assertEqual(p.edid_sha256, "c41a484bac3d25c33d3ec6e84e3fe714adc47b10668d70c9388c90502804cb87")
        self.assertEqual(p.hdmi_audio_default, "disabled")
        self.assertEqual(p.physical_aspect, (4, 3))
        self.assertAlmostEqual(p.field_rate_hz(), 59.94, places=2)
        self.assertAlmostEqual(p.line_rate_khz(), 15.734, places=3)
        # porches: front 59, sync 62, back 17
        self.assertEqual((p.h[1] - p.h[0], p.h[2] - p.h[1], p.h[3] - p.h[2]), (59, 62, 17))

    def test_list_profiles(self):
        self.assertIn("sony-trinitron-lontium", profile.list_profiles(support.REPO))

    def _bad(self, old, new):
        self.assertIn(old, GOOD)
        with self.assertRaises(profile.ProfileError):
            profile.parse_text(GOOD.replace(old, new, 1))

    def test_rejects_shell_syntax(self):
        self._bad('TV_MODEL="Sony Trinitron, exact model not recorded"', 'TV_MODEL="$(reboot)"')
        self._bad('TV_MODEL="Sony Trinitron, exact model not recorded"', 'TV_MODEL="`id`"')
        self._bad('TV_MODEL="Sony Trinitron, exact model not recorded"', 'TV_MODEL="a\\"b"')
        self._bad("XRANDR_OUTPUT=HDMI-1", "XRANDR_OUTPUT=HDMI-1;reboot")
        self._bad("XRANDR_OUTPUT=HDMI-1", "XRANDR_OUTPUT='HDMI-1'")
        self._bad("MODE_NAME=720x480i-crt", "MODE_NAME=720x480i crt")

    def test_rejects_unknown_duplicate_and_missing_keys(self):
        with self.assertRaises(profile.ProfileError):
            profile.parse_text(GOOD + "\nEXTRA=1\n")
        with self.assertRaises(profile.ProfileError):
            profile.parse_text(GOOD + "\nMARGIN_LEFT=8\n")
        with self.assertRaises(profile.ProfileError):
            profile.parse_text(GOOD.replace("MARGIN_TOP=16\n", ""))

    def test_rejects_inconsistent_values(self):
        self._bad("EXPECTED_DRM_FLAGS=0x101a", "EXPECTED_DRM_FLAGS=0x1a")  # flags mismatch
        self._bad("MODE_H=720,779,841,858", "MODE_H=720,841,779,858")  # sync end before start
        self._bad("MODE_H=720,779,841,858", "MODE_H=720,779,841")
        self._bad("MARGIN_LEFT=8", "MARGIN_LEFT=101")
        self._bad("MODE_FLAGS=nhsync,nvsync,interlace,dblclk", "MODE_FLAGS=nhsync,phsync,interlace,dblclk")
        self._bad("MODE_FLAGS=nhsync,nvsync,interlace,dblclk", "MODE_FLAGS=nhsync,nvsync,interlace,turbo")
        self._bad("HDMI_AUDIO_DEFAULT=disabled", "HDMI_AUDIO_DEFAULT=on")
        self._bad("EDID_SHA256=c41a", "EDID_SHA256=C41A")

    def test_profile_name_cannot_escape_directory(self):
        for name in ("../etc/passwd", "a/b", "", "UPPER"):
            with self.assertRaises(profile.ProfileError):
                profile.load(support.REPO, name)

    def test_manifest_round_trip(self):
        p = profile.load(support.REPO, "sony-trinitron-lontium")
        self.assertEqual(profile.from_raw(p.raw), p)


if __name__ == "__main__":
    unittest.main()
