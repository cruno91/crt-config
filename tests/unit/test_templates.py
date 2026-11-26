"""Behaviour of the generated LightDM display-setup script and initramfs hook."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest

import support
from rpicrt import profile as profile_mod
from rpicrt.installer import _sh_quote_args, render_template

FAKE_HELPER = os.path.join(support.REPO, "tests", "mocks", "fake-crt-xrandr")
XRANDR_OK = os.path.join(support.FIXTURES, "xrandr-verbose.archived.txt")


class DisplaySetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rpi-crt-ds.")
        self.log = os.path.join(self.tmp, "calls.log")
        self.logfile = os.path.join(self.tmp, "logs", "display-setup.log")
        self.p = profile_mod.load(support.REPO, "sony-trinitron-lontium")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def render(self, chain="", x_timeout=None):
        p = self.p
        data = render_template(os.path.join(support.REPO, "templates", "display-setup.sh"), {
            "PROFILE_ID": p.id, "XRANDR_OUTPUT": p.xrandr_output, "MODE_NAME": p.mode_name,
            "HELPER": FAKE_HELPER, "HELPER_ARGS": _sh_quote_args(p.helper_args()),
            "DISPLAY_SETUP": "/usr/local/libexec/rpi-crt/display-setup", "LOG_FILE": self.logfile,
            "CHAINED_HOOK": chain, "MARGIN_LEFT": "8", "MARGIN_RIGHT": "48", "MARGIN_TOP": "16",
            "MARGIN_BOTTOM": "16",
        }).decode()
        # Point the script at the mocks (the only test-time change to the generated file).
        lines = data.splitlines(True)
        idx = [i for i, l in enumerate(lines) if l.startswith("PATH=")]
        self.assertEqual(len(idx), 1)
        lines[idx[0]] = "PATH=%s:/usr/bin:/bin\n" % support.MOCK_BIN
        data = "".join(lines)
        if x_timeout is not None:
            data = data.replace("X_TIMEOUT=10", "X_TIMEOUT=%d" % x_timeout)
        path = os.path.join(self.tmp, "display-setup")
        with open(path, "w") as fh:
            fh.write(data)
        os.chmod(path, 0o755)
        return path

    def run_script(self, path, **env):
        e = {"PATH": "/usr/bin:/bin", "DISPLAY": ":0", "RPI_CRT_MOCK_LOG": self.log,
             "RPI_CRT_MOCK_BIN": support.MOCK_BIN, "RPI_CRT_MOCK_XRANDR": XRANDR_OK}
        e.update({k: v for k, v in env.items() if v is not None})
        for k in [k for k, v in env.items() if v is None]:
            e.pop(k, None)
        start = time.time()
        proc = subprocess.run(["/bin/sh", path], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=60)
        calls = support.slurp(self.log).splitlines() if os.path.exists(self.log) else []
        logtext = support.slurp(self.logfile) if os.path.exists(self.logfile) else ""
        return proc.returncode, calls, logtext, time.time() - start

    def test_success(self):
        rc, calls, log, _ = self.run_script(self.render())
        self.assertEqual(rc, 0)
        self.assertIn("applied 720x480i-crt on HDMI-1 with margins L8 R48 T16 B16", log)
        helper = [c for c in calls if c.startswith("crt-xrandr")]
        self.assertEqual(helper[0], "crt-xrandr --output HDMI-1 --name 720x480i-crt --clock 13500 --h 720,779,841,858 "
                                    "--v 480,488,494,525 --flags nhsync,nvsync,interlace,dblclk")
        self.assertTrue(helper[1].endswith("--query --require-active"))
        self.assertIn("xrandr --output HDMI-1 --mode 720x480i-crt --set left margin 8 --set right margin 48 "
                      "--set top margin 16 --set bottom margin 16", calls)
        self.assertTrue(any(c.startswith("logger -t rpi-crt") for c in calls))

    def assert_fallback(self, rc, calls, log, mode_selected):
        self.assertEqual(rc, 0, "display-setup must never fail the LightDM seat")
        self.assertIn("FALLBACK", log)
        self.assertEqual(any(c.startswith("xrandr --output") for c in calls), mode_selected)

    def test_conflicting_mode(self):
        rc, calls, log, _ = self.run_script(self.render(), RPI_CRT_MOCK_HELPER_RC="3")
        self.assert_fallback(rc, calls, log, False)
        self.assertIn("already exists", log)

    def test_no_x_server(self):
        rc, calls, log, _ = self.run_script(self.render(), RPI_CRT_MOCK_XRANDR=None)
        self.assert_fallback(rc, calls, log, False)
        self.assertFalse(any(c.startswith("crt-xrandr") for c in calls))

    def test_output_disconnected(self):
        fx = os.path.join(self.tmp, "x.txt")
        with open(fx, "w") as fh:
            fh.write(support.slurp(XRANDR_OK).replace("HDMI-1 connected", "HDMI-1 disconnected"))
        rc, calls, log, _ = self.run_script(self.render(), RPI_CRT_MOCK_XRANDR=fx)
        self.assert_fallback(rc, calls, log, False)
        self.assertIn("not connected", log)

    def test_mode_selection_fails(self):
        rc, calls, log, _ = self.run_script(self.render(), RPI_CRT_MOCK_XRANDR_SET_FAIL="1")
        self.assert_fallback(rc, calls, log, True)

    def test_mode_not_active_after_selection(self):
        rc, calls, log, _ = self.run_script(self.render(), RPI_CRT_MOCK_HELPER_QUERY_RC="5")
        self.assert_fallback(rc, calls, log, True)
        self.assertIn("is not active", log)

    def test_chained_hook_runs_first_and_keeps_status(self):
        hook = os.path.join(self.tmp, "hook.sh")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\necho chained >> %s\nexit 7\n" % self.log)
        os.chmod(hook, 0o755)
        rc, calls, log, _ = self.run_script(self.render(chain=hook))
        self.assertEqual(rc, 7)
        self.assertLess(calls.index("chained"), min(i for i, c in enumerate(calls) if c.startswith("crt-xrandr")))
        self.assertIn("applied", log)

    def test_missing_chained_hook(self):
        rc, _, log, _ = self.run_script(self.render(chain="/nonexistent/hook"))
        self.assertEqual(rc, 0)
        self.assertIn("missing or not executable", log)

    @unittest.skipUnless(shutil.which("timeout"), "coreutils timeout(1) not available")
    def test_hung_x_calls_time_out(self):
        rc, calls, log, elapsed = self.run_script(self.render(x_timeout=1), RPI_CRT_MOCK_HELPER_SLEEP="30")
        self.assert_fallback(rc, calls, log, False)
        self.assertLess(elapsed, 10)

    def test_log_is_bounded(self):
        os.makedirs(os.path.dirname(self.logfile))
        with open(self.logfile, "w") as fh:
            fh.write("x" * 300000 + "\n" + "old line\n" * 1000)
        self.run_script(self.render())
        self.assertLess(os.path.getsize(self.logfile), 300000)


@unittest.skipUnless(os.environ.get("RPI_CRT_CONTAINER") == "1" and os.geteuid() == 0,
                     "runs only as root inside the disposable test container")
class InitramfsHookTests(unittest.TestCase):
    def setUp(self):
        self.dest = tempfile.mkdtemp(prefix="initramfs.")
        hook = render_template(os.path.join(support.REPO, "templates", "initramfs-hook.sh"),
                               {"PROFILE_ID": "t", "EDID_FILES": "converter-crt.bin"})
        self.hook = os.path.join(self.dest + ".hook")
        with open(self.hook, "wb") as fh:
            fh.write(hook)
        os.chmod(self.hook, 0o755)

    def tearDown(self):
        shutil.rmtree(self.dest, ignore_errors=True)
        os.unlink(self.hook)
        if os.path.exists("/lib/firmware/edid/converter-crt.bin"):
            os.unlink("/lib/firmware/edid/converter-crt.bin")

    def test_prereqs(self):
        r = subprocess.run([self.hook, "prereqs"], stdout=subprocess.PIPE, universal_newlines=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))

    def test_copies_firmware(self):
        os.makedirs("/lib/firmware/edid", exist_ok=True)
        shutil.copy(os.path.join(support.REPO, "edid", "converter-crt.bin"), "/lib/firmware/edid/")
        r = subprocess.run([self.hook], env={"DESTDIR": self.dest, "PATH": "/usr/bin:/bin"})
        self.assertEqual(r.returncode, 0)
        with open(os.path.join(self.dest, "lib/firmware/edid/converter-crt.bin"), "rb") as a, \
                open(os.path.join(support.REPO, "edid", "converter-crt.bin"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_missing_firmware_fails_loudly(self):
        r = subprocess.run([self.hook], env={"DESTDIR": self.dest, "PATH": "/usr/bin:/bin"},
                           stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("is missing", r.stderr)


if __name__ == "__main__":
    unittest.main()
