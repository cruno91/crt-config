import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

import support
from rpicrt import edid, parsers

CRT = support.fixture("../../edid/converter-crt.bin", "rb")
ORIG = support.fixture("../../edid/lontium-original.bin", "rb")
GEN = os.path.join(support.REPO, "edid", "generate-edid.py")


class EdidTests(unittest.TestCase):
    def test_hashes_match_archive(self):
        self.assertEqual(hashlib.sha256(CRT).hexdigest(),
                         "c41a484bac3d25c33d3ec6e84e3fe714adc47b10668d70c9388c90502804cb87")
        self.assertEqual(hashlib.sha256(ORIG).hexdigest(),
                         "e6767693ade654c6aef4a6eba51c27818099e7a54a3d07eefa221bb3782955dd")

    def test_generator_reproduces_bytes(self):
        res = subprocess.run([sys.executable, "-I", GEN, "--check"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(res.returncode, 0, res.stdout)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.bin")
            subprocess.run([sys.executable, "-I", GEN, "--output", out], check=True, stdout=subprocess.PIPE)
            with open(out, "rb") as fh:
                self.assertEqual(fh.read(), CRT)

    def test_generator_matches_archived_script_semantics(self):
        """The archived script (reference/) must produce the same bytes."""
        with tempfile.TemporaryDirectory() as home:
            with open(os.path.join(home, "converter.edid"), "wb") as fh:
                fh.write(ORIG)
            env = {"HOME": home, "PATH": "/usr/bin:/bin"}
            subprocess.run([sys.executable, "-I", os.path.join(support.REPO, "reference", "patch-edid-crt.py")],
                           env=env, check=True, stdout=subprocess.PIPE)
            with open(os.path.join(home, "converter-crt.edid"), "rb") as fh:
                self.assertEqual(fh.read(), CRT)

    def test_generator_rejects_bad_input(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.bin")
            with open(bad, "wb") as fh:
                fh.write(ORIG[:128])
            res = subprocess.run([sys.executable, "-I", GEN, "--input", bad, "--output", bad + ".out"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(res.returncode, 2)

    def test_restricted_edid_structure(self):
        self.assertEqual(edid.restriction_problems(CRT), [])
        info = edid.inspect(CRT)
        self.assertEqual(info["monitor_name"], "LONTIUM")
        self.assertEqual(info["cta"]["vics"], [6])
        self.assertEqual(info["cta"]["native_vics"], [6])
        self.assertEqual(info["cta"]["audio_blocks"], 1)        # LPCM kept
        self.assertEqual(info["cta"]["vendor_blocks"], ["000c03"])  # HDMI VSDB kept
        self.assertTrue(all(ok for _, ok in info["checksums"]))

    def test_original_edid_is_not_restricted(self):
        problems = edid.restriction_problems(ORIG)
        self.assertTrue(any("VICs" in p for p in problems))
        self.assertTrue(any("detailed timing" in p for p in problems))
        self.assertEqual(sorted(edid.inspect(ORIG)["cta"]["vics"])[-3:], [4, 5, 16])

    def test_checksum_corruption_detected(self):
        bad = bytearray(CRT)
        bad[200] ^= 0xFF
        self.assertIn("block 1 checksum is invalid", edid.restriction_problems(bytes(bad)))
        with self.assertRaises(edid.EdidError):
            edid.inspect(b"\x00" * 128)


class XrandrParserTests(unittest.TestCase):
    def test_archived_verbose_output(self):
        outs = parsers.parse_xrandr_verbose(support.fixture("xrandr-verbose.archived.txt"))
        hdmi = outs["HDMI-1"]
        self.assertTrue(hdmi["connected"])
        self.assertFalse(outs["HDMI-2"]["connected"])
        self.assertEqual(hdmi["current_mode_id"], "0x20e")
        cur = [m for m in hdmi["modes"] if m["current"]]
        self.assertEqual(len(cur), 1)
        m = cur[0]
        self.assertEqual((m["name"], m["h"], m["v"], m["clock_mhz"]),
                         ("720x480i-crt", (720, 779, 841, 858), (480, 488, 494, 525), 13.5))
        self.assertEqual(m["flags"], ["-HSync", "-VSync", "Interlace"])
        self.assertAlmostEqual(m["refresh_hz"], 59.94)
        for k, v in (("left", "8"), ("right", "48"), ("top", "16"), ("bottom", "16")):
            self.assertEqual(hdmi["props"]["%s margin" % k], v)
        self.assertEqual(outs["HDMI-2"]["props"]["left margin"], "0")
        self.assertEqual(len(hdmi["modes"]), 3)


class DrmStateParserTests(unittest.TestCase):
    def test_active_crtc_and_duplicates(self):
        st = parsers.parse_drm_state(support.fixture("drm-state.active.txt"))
        self.assertEqual(len(st["crtcs"]), 6)
        crtc, why = parsers.active_crtc_for(st, "HDMI-A-1")
        self.assertEqual(why, "ok")
        self.assertEqual(crtc["name"], "crtc-3")
        self.assertEqual(crtc["mode"]["clock"], 13500)
        self.assertEqual(crtc["mode"]["h"], (720, 779, 841, 858))
        self.assertEqual(crtc["mode"]["flags"], 0x101A)
        self.assertEqual(parsers.active_crtc_for(st, "HDMI-A-2")[0], None)
        self.assertIn("not present", parsers.active_crtc_for(st, "HDMI-A-3")[1])

    def test_inactive_crtc_rejected(self):
        text = support.fixture("drm-state.active.txt").replace("enable=1\n\tactive=1", "enable=1\n\tactive=0")
        crtc, why = parsers.active_crtc_for(parsers.parse_drm_state(text), "HDMI-A-1")
        self.assertIsNone(crtc)
        self.assertIn("not enabled and active", why)


if __name__ == "__main__":
    unittest.main()
