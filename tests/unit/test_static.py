"""Static checks: shell syntax/lint, Python compatibility, provenance hashes."""

import ast
import glob
import hashlib
import os
import shutil
import subprocess
import unittest

import support

R = support.REPO
SHELL = [os.path.join(R, f) for f in ("install.sh", "verify.sh", "uninstall.sh",
                                       "templates/display-setup.sh", "templates/initramfs-hook.sh",
                                       "tests/playback.sh", "tests/test-installer.sh", "tests/run-container.sh")]


class StaticTests(unittest.TestCase):
    def test_shell_syntax(self):
        for f in SHELL:
            with self.subTest(f=f):
                self.assertEqual(subprocess.run(["sh", "-n", f]).returncode, 0)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck not installed")
    def test_shellcheck(self):
        r = subprocess.run(["shellcheck", "-s", "sh", "-S", "warning"] + SHELL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_python_parses_for_older_interpreters(self):
        """The tested OS ships Python 3.13; keep the code parseable by 3.9 too."""
        files = glob.glob(os.path.join(R, "lib/rpicrt/*.py")) + [os.path.join(R, "edid/generate-edid.py"),
                                                                 os.path.join(R, "tests/geometry.py")]
        for f in files:
            with self.subTest(f=f):
                with open(f) as fh:
                    ast.parse(fh.read(), f, feature_version=(3, 9))

    def test_reference_files_match_archive(self):
        """reference/ holds verbatim archive members (except redacted ones)."""
        sums = {}
        with open(os.path.join(R, "reference", "ARCHIVE-SHA256SUMS")) as fh:
            for line in fh:
                h, name = line.split()
                sums[name] = h
        verbatim = {
            "crt-xrandr.c": "home/USER/crt-xrandr.c",
            "crt-shift.c": "home/USER/crt-shift.c",
            "patch-edid-crt.py": "home/USER/patch-edid-crt.py",
            "crt-display-setup.sh": "usr/local/bin/crt-display-setup.sh",
            "initramfs-hook-crt-edid": "etc/initramfs-tools/hooks/crt-edid",
            "config.txt": "boot/firmware/config.txt",
            "../edid/converter-crt.bin": "lib/firmware/edid/converter-crt.bin",
            "../edid/lontium-original.bin": "home/USER/converter.edid",
        }
        for local, member in verbatim.items():
            with self.subTest(local=local), open(os.path.join(R, "reference", local), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), sums[member])

    def test_no_machine_identifiers_committed(self):
        import re
        bad = re.compile(r"PARTUUID=(?!REDACTED|0badc0de)[0-9a-f]{8}|smsc95xx\.macaddr|(?:[0-9A-F]{2}:){5}[0-9A-F]{2}")
        for d, _, files in os.walk(R):
            if "/.git" in d:
                continue
            for f in files:
                p = os.path.join(d, f)
                if p.endswith((".bin", ".pyc")):
                    continue
                with open(p, "rb") as fh:
                    text = fh.read().decode("utf-8", "replace")
                with self.subTest(p=p):
                    self.assertIsNone(bad.search(text))

    def test_profile_and_templates_placeholders(self):
        for t in ("display-setup.sh", "initramfs-hook.sh"):
            with open(os.path.join(R, "templates", t)) as fh:
                self.assertIn("@@PROFILE_ID@@", fh.read())

    def test_entry_points_executable(self):
        for f in ("install.sh", "verify.sh", "uninstall.sh", "edid/generate-edid.py", "tests/geometry.py",
                  "tests/playback.sh", "tests/test-installer.sh", "tests/run-container.sh"):
            with self.subTest(f=f):
                self.assertTrue(os.access(os.path.join(R, f), os.X_OK))


if __name__ == "__main__":
    unittest.main()
