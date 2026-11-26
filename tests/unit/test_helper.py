"""crt-xrandr: equivalence with the archived helper and the new logic.

Both helpers are compiled against the real X11/XRandR headers and linked with
tests/stubs/fake_xrandr.c, a stateful fake server, so the exact XRRModeInfo
each one sends can be compared. Requires a C compiler and the libx11-dev /
libxrandr-dev headers (present in tests/container); skipped otherwise.
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest

import support

SRC = os.path.join(support.REPO, "src", "crt-xrandr.c")
ORIG = os.path.join(support.REPO, "reference", "crt-xrandr.c")
STUB = os.path.join(support.REPO, "tests", "stubs", "fake_xrandr.c")
SHIFT = os.path.join(support.REPO, "src", "crt-shift.c")


def _pkg(*args):
    try:
        return subprocess.run(["pkg-config"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              universal_newlines=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None


CFLAGS = _pkg("--cflags", "x11", "xrandr")
HAVE_X_HEADERS = CFLAGS is not None and shutil.which("cc") is not None and \
    subprocess.run(["cc", "-fsyntax-only", "-x", "c", "-"] + (CFLAGS or []), input="#include <X11/extensions/Xrandr.h>\n",
                   universal_newlines=True, stderr=subprocess.DEVNULL).returncode == 0

BASE_STATE = """next 200
output 44 HDMI-1 60
output 45 HDMI-2 0
outmode 44 47
outmode 44 48
crtc 60 47
crtc 61 0
mode 47 720x480i 13514000 720 739 801 858 0 480 488 494 525 101a
mode 48 720x480i 13500000 720 739 801 858 0 480 488 494 525 101a
"""


@unittest.skipUnless(HAVE_X_HEADERS, "C compiler or X11/XRandR development headers not available")
class HelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="crt-xrandr.")
        cls.new = os.path.join(cls.dir, "new")
        cls.orig = os.path.join(cls.dir, "orig")
        for out, src, extra in ((cls.new, SRC, ["-Wextra", "-Werror"]), (cls.orig, ORIG, [])):
            subprocess.run(["cc", "-Wall", "-O2"] + extra + ["-o", out, src, STUB] + CFLAGS, check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.state = os.path.join(self.dir, "state")
        self.log = os.path.join(self.dir, "log")
        self.reset()

    def reset(self, state=BASE_STATE):
        with open(self.state, "w") as fh:
            fh.write(state)
        if os.path.exists(self.log):
            os.unlink(self.log)

    def run_helper(self, exe, *args, **env):
        e = {"FAKE_X_STATE": self.state, "FAKE_X_LOG": self.log, "PATH": "/usr/bin:/bin"}
        e.update(env)
        r = subprocess.run([exe] + list(args), env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=30)
        calls = support.slurp(self.log).splitlines() if os.path.exists(self.log) else []
        return r.returncode, r.stdout + r.stderr, calls

    def test_new_helper_sends_exactly_the_archived_request(self):
        rc_o, out_o, calls_o = self.run_helper(self.orig)
        self.assertEqual(rc_o, 0, out_o)
        self.reset()
        rc_n, out_n, calls_n = self.run_helper(self.new)
        self.assertEqual(rc_n, 0, out_n)
        create = [c for c in calls_o if c.startswith("CreateMode")]
        self.assertEqual(create, ["CreateMode name=720x480i-crt dotClock=13500000 h=720,779,841,858 hskew=0 "
                                  "v=480,488,494,525 flags=0x101a"])
        self.assertEqual(calls_n, calls_o)
        self.assertIn("Custom mode registered: 0x200", out_n)
        self.assertIn("Mode flags: 0x101a", out_n)

    def test_profile_arguments_equal_defaults(self):
        self.run_helper(self.new)
        default_calls = support.slurp(self.log)
        self.reset()
        p = __import__("rpicrt.profile", fromlist=["load"]).load(support.REPO, "sony-trinitron-lontium")
        rc, out, _ = self.run_helper(self.new, *p.helper_args())
        self.assertEqual(rc, 0, out)
        self.assertEqual(support.slurp(self.log), default_calls)
        rc, out, _ = self.run_helper(self.new, *(p.helper_args() + ["--print"]))
        self.assertEqual(out.strip(), "name=720x480i-crt clock_khz=13500 h=720,779,841,858 v=480,488,494,525 hskew=0 flags=0x101a")

    def test_idempotent_and_query_states(self):
        self.assertEqual(self.run_helper(self.new, "--query")[0], 6)          # absent
        self.run_helper(self.new)
        os.unlink(self.log)
        rc, out, calls = self.run_helper(self.new)
        self.assertEqual(rc, 0)
        self.assertIn("already present", out)
        self.assertEqual(calls, ["GetScreenResources"])  # nothing created or attached again
        rc, out, _ = self.run_helper(self.new, "--query")
        self.assertEqual(rc, 0)
        self.assertIn("state=attached", out)
        self.assertEqual(self.run_helper(self.new, "--query", "--require-active")[0], 5)
        with open(self.state) as fh:
            text = fh.read().replace("crtc 60 47", "crtc 60 200")
        with open(self.state, "w") as fh:
            fh.write(text)
        rc, out, calls = self.run_helper(self.new, "--query", "--require-active")
        self.assertEqual(rc, 0)
        self.assertIn("state=active", out)
        self.assertEqual(calls[-1], "GetScreenResourcesCurrent")  # read-only, no re-probe

    def test_conflicting_mode(self):
        self.reset(BASE_STATE + "mode 99 720x480i-crt 13500000 720 739 801 858 0 480 488 494 525 101a\noutmode 44 99\n")
        rc, out, calls = self.run_helper(self.new)
        self.assertEqual(rc, 3)
        self.assertIn("--replace", out)
        self.assertFalse(any(c.startswith("CreateMode") for c in calls))
        self.assertEqual(self.run_helper(self.new, "--query")[0], 3)
        rc, out, calls = self.run_helper(self.new, "--replace")
        self.assertEqual(rc, 0, out)
        self.assertEqual([c.split()[0] for c in calls if not c.startswith("GetScreen")][-4:],
                         ["DeleteOutputMode", "DestroyMode", "CreateMode", "AddOutputMode"])
        self.assertEqual(self.run_helper(self.new, "--query")[0], 0)

    def test_conflicting_mode_in_use_is_not_destroyed(self):
        self.reset(BASE_STATE.replace("crtc 60 47", "crtc 60 99")
                   + "mode 99 720x480i-crt 13500000 720 739 801 858 0 480 488 494 525 101a\noutmode 44 99\n")
        rc, out, calls = self.run_helper(self.new, "--replace")
        self.assertEqual(rc, 3)
        self.assertIn("is in use", out)
        self.assertFalse(any(c.startswith("DestroyMode") for c in calls))

    def test_errors(self):
        self.assertEqual(self.run_helper(self.new, "--output", "HDMI-9")[0], 4)
        self.assertEqual(self.run_helper(self.new, FAKE_X_FAIL="CreateMode")[0], 1)
        self.reset()
        self.assertEqual(self.run_helper(self.new, FAKE_X_FAIL="AddOutputMode")[0], 1)
        self.assertEqual(self.run_helper(self.new, FAKE_X_NODISPLAY="1")[0], 1)
        for bad in (["--h", "720,,841,858"], ["--h", "720,841,779,858"], ["--clock", "-5"], ["--flags", "dblclk,x"],
                    ["--flags", "phsync,nhsync"], ["--bogus"], ["--output"], ["--replace", "--query"],
                    ["--require-active"]):
            with self.subTest(args=bad):
                self.assertEqual(self.run_helper(self.new, *bad)[0], 2)

    def test_print_needs_no_display(self):
        rc, out, calls = self.run_helper(self.new, "--print", FAKE_X_NODISPLAY="1")
        self.assertEqual((rc, calls), (0, []))


@unittest.skipUnless(HAVE_X_HEADERS and shutil.which("Xvfb"), "Xvfb not available")
class XvfbAcceptanceTests(unittest.TestCase):
    """A real X server accepts the DoubleClock mode request.

    Xvfb does not keep user modes after the client disconnects (even
    xrandr --newmode/--addmode fails there), so only creation is checked.
    """

    def test_real_server_accepts_mode(self):
        d = tempfile.mkdtemp(prefix="xvfb.")
        try:
            exe = os.path.join(d, "crt-xrandr")
            subprocess.run(["cc", "-O2", "-o", exe, SRC] + _pkg("--cflags", "--libs", "xrandr", "x11"), check=True)
            xvfb = subprocess.Popen(["Xvfb", ":93", "-screen", "0", "1024x768x24"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                env = {"DISPLAY": ":93", "PATH": "/usr/bin:/bin"}
                for _ in range(50):
                    if subprocess.run(["xrandr", "--query"], env=env, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL).returncode == 0:
                        break
                    time.sleep(0.1)
                r = subprocess.run([exe, "--output", "screen"], env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, universal_newlines=True, timeout=20)
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertIn("Custom mode registered", r.stdout)
                self.assertNotIn("X11 error", r.stdout)
                r = subprocess.run([exe, "--output", "nope"], env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, universal_newlines=True, timeout=20)
                self.assertEqual(r.returncode, 4)
            finally:
                xvfb.terminate()
                xvfb.wait(10)
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(_pkg("--cflags", "--libs", "libdrm") is not None and shutil.which("cc"), "libdrm headers not available")
class CrtShiftBuildTests(unittest.TestCase):
    def test_compiles_cleanly_and_validates_args(self):
        d = tempfile.mkdtemp(prefix="crt-shift.")
        try:
            exe = os.path.join(d, "crt-shift")
            subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-O2", "-o", exe, SHIFT]
                           + _pkg("--cflags", "--libs", "libdrm"), check=True)
            r = subprocess.run([exe, "--shift", "41"], stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn("between -10 and 40", r.stderr)
            self.assertEqual(subprocess.run([exe, "--help"], stdout=subprocess.DEVNULL).returncode, 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
