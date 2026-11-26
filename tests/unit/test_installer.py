"""End-to-end installer/verifier/uninstaller tests against staged roots."""

import json
import os
import unittest

import support
from support import cli, read, write, exists, snapshot

P = ("--profile", "sony-trinitron-lontium")
OURS = "/usr/local/libexec/rpi-crt/display-setup"
STATE = "/var/lib/rpi-crt"
RUNTIME = ("proc/", "sys/", "boot/firmware/initramfs", "var/")


def config_view(snap):
    """Snapshot without simulated runtime files and generated initramfs listings."""
    return {k: v for k, v in snap.items() if not k.startswith(RUNTIME)}


class Base(unittest.TestCase):
    variant = "stock"

    def setUp(self):
        self.root = support.make_root(self.variant)
        self.log = self.root + ".log"
        self.before = snapshot(self.root)

    def tearDown(self):
        support.cleanup(self.root)
        if os.path.exists(self.log):
            os.unlink(self.log)

    def install(self, *args, env=None, ok=True):
        r = cli("install", self.root, *(P + args), env=env, log=self.log)
        if ok:
            self.assertEqual(r.rc, 0, r)
        return r

    def manifest(self):
        return json.loads(read(self.root, STATE + "/manifest.json"))

    def calls(self, prefix):
        if not os.path.exists(self.log):
            return []
        return [l for l in support.slurp(self.log).splitlines() if l.startswith(prefix)]

    def assert_unchanged(self, exclude_state=True):
        after = snapshot(self.root)
        diff = sorted(k for k in set(self.before) | set(after)
                      if self.before.get(k) != after.get(k)
                      and not (exclude_state and k.startswith("var/")))
        self.assertEqual(diff, [])


class FreshInstallTests(Base):
    def test_dry_run_changes_nothing(self):
        r = self.install("--dry-run")
        self.assertIn("Dry run: nothing was changed.", r.out)
        self.assertIn("+dtoverlay=vc4-kms-v3d,noaudio", r.out)
        self.assertIn("drm.edid_firmware=HDMI-A-1:edid/converter-crt.bin video=HDMI-A-1:720x480@60iD", r.out)
        self.assert_unchanged(exclude_state=False)
        self.assertEqual(self.calls("update-initramfs") + self.calls("apt-get") + self.calls("gcc"), [])

    def test_install_result(self):
        self.install()
        cmd = read(self.root, "/boot/firmware/cmdline.txt")
        self.assertEqual(cmd, support.fixture("cmdline.txt.stock").rstrip("\n")
                         + " drm.edid_firmware=HDMI-A-1:edid/converter-crt.bin video=HDMI-A-1:720x480@60iD\n")
        self.assertIn("\ndtoverlay=vc4-kms-v3d,noaudio\n", read(self.root, "/boot/firmware/config.txt"))
        self.assertIn("\ndisplay-setup-script=%s\n" % OURS, read(self.root, "/etc/lightdm/lightdm.conf"))
        self.assertEqual(read(self.root, "/lib/firmware/edid/converter-crt.bin", "rb"),
                         support.fixture("../../edid/converter-crt.bin", "rb"))
        setup = read(self.root, OURS)
        self.assertNotIn("@@", setup)
        self.assertIn("CHAIN='/usr/share/dispsetup.sh'", setup)
        self.assertIn("'--h' '720,779,841,858' '--v' '480,488,494,525' '--flags' 'nhsync,nvsync,interlace,dblclk'", setup)
        self.assertIn('--set "right margin" 48', setup)
        hook = read(self.root, "/etc/initramfs-tools/hooks/rpi-crt-edid")
        self.assertIn("for file in converter-crt.bin; do", hook)
        self.assertNotIn("converter-480i", hook)
        for path, mode in ((OURS, 0o755), ("/usr/local/libexec/rpi-crt/crt-xrandr", 0o755),
                           ("/lib/firmware/edid/converter-crt.bin", 0o644),
                           ("/etc/initramfs-tools/hooks/rpi-crt-edid", 0o755)):
            self.assertEqual(os.stat(os.path.join(self.root, path.lstrip("/"))).st_mode & 0o777, mode, path)
        self.assertEqual(read(self.root, "/boot/firmware/cmdline.txt.rpi-crt-baseline"), support.fixture("cmdline.txt.stock"))
        m = self.manifest()
        self.assertEqual(m["state"], "installed")
        self.assertEqual(m["edits"]["cmdline"]["record"]["edid"]["prior"], None)
        self.assertTrue(all(e["status"] == "applied" for e in m["files"].values()))
        self.assertEqual(oct(os.stat(os.path.join(self.root, STATE.lstrip("/"))).st_mode & 0o777), "0o700")
        # ordering: initramfs rebuilt before the boot arguments are written
        self.assertEqual(len(self.calls("update-initramfs")), 1)

    def test_reinstall_is_idempotent_and_keeps_baseline(self):
        self.install()
        snap1 = snapshot(self.root, exclude=())
        baseline1 = {k: v for k, v in snap1.items() if "/baseline/" in k}
        n_initramfs = len(self.calls("update-initramfs"))
        r = self.install()
        snap2 = snapshot(self.root, exclude=())
        changed = sorted(k for k in set(snap1) | set(snap2)
                         if snap1.get(k) != snap2.get(k) and not k.startswith("var/lib/rpi-crt/runs")
                         and k != "var/lib/rpi-crt/manifest.json")
        self.assertEqual(changed, [])
        self.assertEqual(baseline1, {k: v for k, v in snap2.items() if "/baseline/" in k})
        self.assertEqual(len(self.calls("update-initramfs")), n_initramfs)  # nothing to rebuild
        self.assertNotIn("  edited", r.out)
        self.assertEqual(read(self.root, "/boot/firmware/cmdline.txt").count("drm.edid_firmware"), 1)
        self.assertEqual(self.manifest()["edits"]["lightdm"]["record"]["prior_line"],
                         "display-setup-script=/usr/share/dispsetup.sh")

    def test_uninstall_restores_original_and_allows_reinstall(self):
        for _ in range(2):
            self.install()
            r = cli("uninstall", self.root, log=self.log)
            self.assertEqual(r.rc, 0, r)
            self.assertEqual(config_view(snapshot(self.root)), config_view(self.before))
            self.assertFalse(exists(self.root, STATE + "/manifest.json"))
            self.assertTrue(os.listdir(os.path.join(self.root, STATE.lstrip("/"), "history")))

    @unittest.skipUnless(os.path.exists("/usr/bin/gcc") and os.path.exists("/usr/bin/pkg-config")
                         and __import__("test_helper").HAVE_X_HEADERS, "needs gcc with X11/XRandR headers")
    def test_install_with_real_compiler(self):
        self.install(env={"RPI_CRT_MOCK_REAL_GCC": "1"})
        helper = read(self.root, "/usr/local/libexec/rpi-crt/crt-xrandr", "rb")
        self.assertEqual(helper[:4], b"\x7fELF")
        st = json.loads(cli("verify", self.root, "--json").out)
        self.assertEqual({c["id"]: c["status"] for c in st["checks"]}["install.helper"], "PASS")

    def test_uninstall_dry_run(self):
        self.install()
        snap = snapshot(self.root, exclude=())
        r = cli("uninstall", self.root, "--dry-run", log=self.log)
        self.assertEqual(r.rc, 0, r)
        self.assertIn("restoring original settings in /boot/firmware/cmdline.txt", r.out)
        self.assertEqual(snapshot(self.root, exclude=()), snap)

    def test_vfat_modes_do_not_break_idempotence_or_verify(self):
        """On vfat every boot-partition file reports 0755; emulate that after install."""
        self.install()
        for f in ("cmdline.txt.rpi-crt-baseline", "config.txt.rpi-crt-baseline"):
            os.chmod(os.path.join(self.root, "boot/firmware", f), 0o755)
        m = self.manifest()
        for f in ("cmdline.txt.rpi-crt-baseline", "config.txt.rpi-crt-baseline"):
            m["files"]["/boot/firmware/" + f]["mode"] = "0755"  # as recorded after a write on vfat
        write(self.root, STATE + "/manifest.json", json.dumps(m), 0o600)
        r = self.install()
        self.assertNotIn("  installed", r.out)
        st = {c["id"]: c["status"] for c in json.loads(cli("verify", self.root, *P, "--json").out)["checks"]}
        self.assertEqual(st["install.files"], "PASS")

    def test_uninstall_without_install(self):
        r = cli("uninstall", self.root)
        self.assertEqual(r.rc, 1)
        self.assertIn("nothing to uninstall", r.err)


class VerifyTests(Base):
    def verify(self, *args, env=None):
        r = cli("verify", self.root, *(P + args), env=env)
        return r

    def statuses(self, r):
        data = json.loads(r.out)
        return {c["id"]: c["status"] for c in data["checks"]}, data

    def test_not_installed(self):
        r = self.verify("--json")
        st, data = self.statuses(r)
        self.assertEqual(r.rc, 1)
        self.assertEqual(st["install.manifest"], "FAIL")
        self.assertEqual(st["drm.modes"], "FAIL")  # original EDID exposes 1080p/720p

    def test_pending_before_reboot(self):
        self.install()
        r = self.verify("--json")
        st, data = self.statuses(r)
        self.assertEqual(r.rc, 2, r)
        self.assertEqual(data["result"], "incomplete")
        for cid in ("cmdline.running", "edid.active", "audio.policy", "reboot.persistence"):
            self.assertEqual(st[cid], "PENDING", cid)
        for cid in ("install.manifest", "install.files", "install.baseline", "install.helper", "lightdm.hook",
                    "edid.file", "initramfs.boot", "initramfs.hooks", "cmdline.file"):
            self.assertEqual(st[cid], "PASS", cid)
        self.assertEqual(st["x11.mode"], "NOT_TESTED")

    def test_pass_after_reboot(self):
        self.install()
        xr = support.boot_after_install(self.root)
        r = self.verify("--json", env={"RPI_CRT_MOCK_XRANDR": xr})
        st, data = self.statuses(r)
        self.assertEqual(r.rc, 0, r.out)
        self.assertTrue(all(s == "PASS" for c, s in st.items() if not c.startswith("manual.")), st)
        self.assertTrue(all(s == "NOT_TESTED" for c, s in st.items() if c.startswith("manual.")))
        text = self.verify(env={"RPI_CRT_MOCK_XRANDR": xr})
        self.assertIn("Result: PASS", text.out)

    def test_detects_wrong_runtime_state(self):
        self.install()
        xr = support.boot_after_install(self.root)
        bad_x = os.path.join(self.root, "x.txt")
        write(self.root, "/x.txt", support.slurp(xr).replace("left margin: 8 ", "left margin: 0 "))
        write(self.root, "/sys/kernel/debug/dri/1/state",
              support.fixture("drm-state.active.txt").replace("720 779 841 858", "720 739 801 858"))
        write(self.root, "/sys/class/drm/card1-HDMI-A-1/modes", "720x480i\n1280x720\n")
        r = self.verify("--json", env={"RPI_CRT_MOCK_XRANDR": bad_x})
        st, _ = self.statuses(r)
        self.assertEqual(r.rc, 1)
        for cid in ("x11.margins", "drm.active", "drm.modes", "reboot.persistence"):
            self.assertEqual(st[cid], "FAIL", cid)
        self.assertEqual(st["x11.mode"], "PASS")

    def test_detects_tampering(self):
        self.install()
        write(self.root, OURS, "#!/bin/sh\nexit 0\n", 0o755)
        os.unlink(os.path.join(self.root, STATE.lstrip("/"), "baseline/boot/firmware/cmdline.txt"))
        cmd = read(self.root, "/boot/firmware/cmdline.txt").rstrip("\n") + " video=HDMI-A-1:720x480@60iD\n"
        write(self.root, "/boot/firmware/cmdline.txt", cmd)
        st, _ = self.statuses(self.verify("--json"))
        self.assertEqual(st["install.files"], "FAIL")
        self.assertEqual(st["install.baseline"], "FAIL")
        self.assertEqual(st["cmdline.file"], "FAIL")

    def test_runtime_only_on_hand_made_setup(self):
        support.cleanup(self.root)
        self.root = support.make_root("legacy")
        xr = support.boot_after_install(self.root)
        r = self.verify("--runtime-only", "--json", env={"RPI_CRT_MOCK_XRANDR": xr})
        st, _ = self.statuses(r)
        self.assertEqual(st["lightdm.hook"], "WARN")
        self.assertEqual(st["x11.doubleclock"], "NOT_TESTED")  # no managed helper; DRM check covers it
        self.assertEqual(st["drm.active"], "PASS")
        self.assertEqual(st["initramfs.hooks"], "PASS")
        self.assertEqual(r.rc, 2)

    def test_legacy_hook_with_missing_firmware_is_flagged(self):
        support.cleanup(self.root)
        self.root = support.make_root("legacy")
        os.unlink(os.path.join(self.root, "lib/firmware/edid/converter-480i.bin"))
        st, _ = self.statuses(self.verify("--runtime-only", "--json"))
        self.assertEqual(st["initramfs.hooks"], "FAIL")


class LegacyMigrationTests(Base):
    variant = "legacy"

    def test_requires_explicit_migration(self):
        r = self.install(ok=False)
        self.assertEqual(r.rc, 1)
        self.assertIn("--migrate-legacy", r.err)
        self.assert_unchanged(exclude_state=False)

    def test_migrate_and_restore(self):
        r = self.install("--migrate-legacy")
        self.assertIn("HDMI audio is already disabled", r.out)
        self.assertFalse(exists(self.root, "/etc/initramfs-tools/hooks/crt-edid"))
        self.assertTrue(exists(self.root, "/usr/local/bin/crt-display-setup.sh"))  # untouched
        self.assertNotIn("config_txt", self.manifest()["edits"])  # nothing owned there
        self.assertEqual(read(self.root, "/boot/firmware/cmdline.txt"), support.fixture("cmdline.txt.legacy"))
        r = cli("uninstall", self.root, log=self.log)
        self.assertEqual(r.rc, 0, r)
        self.assertEqual(config_view(snapshot(self.root)), config_view(self.before))

    def test_missing_480i_firmware_does_not_break_migration(self):
        os.unlink(os.path.join(self.root, "lib/firmware/edid/converter-480i.bin"))
        self.install("--migrate-legacy")
        self.assertEqual(self.manifest()["state"], "installed")


class FailureAndConflictTests(Base):
    def test_interrupted_install_can_resume_or_roll_back(self):
        r = self.install(env={"RPI_CRT_MOCK_INITRAMFS_FAIL": "1"}, ok=False)
        self.assertEqual(r.rc, 1)
        self.assertIn("boot configuration has NOT been changed", r.err)
        self.assertEqual(read(self.root, "/boot/firmware/cmdline.txt"), support.fixture("cmdline.txt.stock"))
        self.assertEqual(self.manifest()["state"], "installing")
        # roll back
        r = cli("uninstall", self.root, log=self.log)
        self.assertEqual(r.rc, 0, r)
        self.assertEqual(config_view(snapshot(self.root)), config_view(self.before))
        # or resume
        self.install(env={"RPI_CRT_MOCK_INITRAMFS_FAIL": "1"}, ok=False)
        r = self.install()
        self.assertIn("resuming an interrupted installation", r.out)
        self.assertEqual(self.manifest()["state"], "installed")
        self.assertEqual(read(self.root, STATE + "/baseline/boot/firmware/cmdline.txt"),
                         support.fixture("cmdline.txt.stock"))

    def test_build_failure_changes_nothing(self):
        r = self.install(env={"RPI_CRT_MOCK_GCC_FAIL": "1"}, ok=False)
        self.assertEqual(r.rc, 1)
        self.assertIn("no system files were changed", r.err)
        self.assert_unchanged()
        self.assertFalse(exists(self.root, STATE + "/manifest.json"))

    def test_packages(self):
        env = {"RPI_CRT_MOCK_MISSING_PKGS": "libxrandr-dev pkg-config"}
        r = self.install("--no-install-deps", env=env, ok=False)
        self.assertIn("missing packages: pkg-config libxrandr-dev", r.err)
        r = self.install(env=dict(env, RPI_CRT_MOCK_APT_FAIL="1"), ok=False)
        self.assertIn("apt-get install failed", r.err)
        self.assert_unchanged()
        self.install(env=env)
        self.assertEqual(self.calls("apt-get")[-1],
                         "apt-get install -y --no-install-recommends pkg-config libxrandr-dev")
        self.assertFalse(any("upgrade" in c for c in self.calls("apt-get")))

    def test_user_edit_conflict_is_reported_not_overwritten(self):
        self.install()
        cmd = read(self.root, "/boot/firmware/cmdline.txt").replace(
            "video=HDMI-A-1:720x480@60iD", "video=HDMI-A-1:720x576@50iD")
        write(self.root, "/boot/firmware/cmdline.txt", cmd, 0o755)
        r = cli("uninstall", self.root, log=self.log)
        self.assertEqual(r.rc, 1)
        self.assertIn("video argument for HDMI-A-1", r.err)
        self.assertEqual(read(self.root, "/boot/firmware/cmdline.txt"), cmd)  # left exactly as edited
        # the line still references the EDID, so the EDID and its hook are kept
        self.assertTrue(exists(self.root, "/lib/firmware/edid/converter-crt.bin"))
        self.assertTrue(exists(self.root, "/etc/initramfs-tools/hooks/rpi-crt-edid"))
        self.assertFalse(exists(self.root, OURS))  # LightDM was restored, so this was removable
        self.assertEqual(self.manifest()["state"], "uninstall-incomplete")
        self.assertIn("uninstall did not finish", self.install(ok=False).err)
        # owner resolves the conflict, uninstall completes
        write(self.root, "/boot/firmware/cmdline.txt", support.fixture("cmdline.txt.stock"), 0o755)
        r = cli("uninstall", self.root, log=self.log)
        self.assertEqual(r.rc, 0, r)
        self.assertEqual(config_view(snapshot(self.root)), config_view(self.before))

    def test_referenced_edid_is_never_removed(self):
        self.install()
        cmd = read(self.root, "/boot/firmware/cmdline.txt").replace(
            "HDMI-A-1:edid/converter-crt.bin", "HDMI-A-1:edid/converter-crt.bin,HDMI-A-2:edid/converter-crt.bin")
        write(self.root, "/boot/firmware/cmdline.txt", cmd)
        r = cli("uninstall", self.root, log=self.log)
        self.assertEqual(r.rc, 1)
        self.assertIn("drm.edid_firmware=HDMI-A-2:edid/converter-crt.bin", read(self.root, "/boot/firmware/cmdline.txt"))
        self.assertTrue(exists(self.root, "/lib/firmware/edid/converter-crt.bin"))
        self.assertTrue(exists(self.root, "/etc/initramfs-tools/hooks/rpi-crt-edid"))
        self.assertIn("still references", r.err)

    def test_modified_installed_file_is_kept(self):
        self.install()
        write(self.root, "/etc/initramfs-tools/hooks/rpi-crt-edid", "#!/bin/sh\n# owner edit\n", 0o755)
        r = cli("uninstall", self.root, log=self.log)
        self.assertEqual(r.rc, 1)
        self.assertIn("modified after installation", r.err)
        self.assertIn("owner edit", read(self.root, "/etc/initramfs-tools/hooks/rpi-crt-edid"))

    def test_unmanaged_existing_edid_is_backed_up_and_restored(self):
        write(self.root, "/lib/firmware/edid/converter-crt.bin", b"someone else's edid")
        self.before = snapshot(self.root)
        self.install()
        r = cli("uninstall", self.root, log=self.log)
        self.assertEqual(r.rc, 0, r)
        self.assertEqual(read(self.root, "/lib/firmware/edid/converter-crt.bin", "rb"), b"someone else's edid")

    def test_lightdm_override_detected_and_reverted(self):
        show = self.root + ".show"
        self.addCleanup(os.unlink, show)
        with open(show, "w") as fh:
            fh.write("[Seat:*]\nA  display-setup-script=/usr/share/dispsetup.sh\nA  user-session=rpd-x\n\nSources:\nA  x\n")
        r = self.install(env={"RPI_CRT_MOCK_LIGHTDM_SHOW": show}, ok=False)
        self.assertEqual(r.rc, 1)
        self.assertIn("the edit was reverted", r.err)
        self.assertEqual(read(self.root, "/etc/lightdm/lightdm.conf"), support.fixture("lightdm.conf.stock"))
        self.assertEqual(read(self.root, "/boot/firmware/cmdline.txt"), support.fixture("cmdline.txt.stock"))
        self.assertEqual(cli("uninstall", self.root, log=self.log).rc, 0)
        self.assertEqual(config_view(snapshot(self.root)), config_view(self.before))

    def test_staged_root_requires_mocks(self):
        r = cli("install", self.root, *P, env={"RPI_CRT_MOCK_BIN": ""})
        self.assertEqual(r.rc, 1)
        self.assertIn("refusing to run real system commands", r.err)


class PreflightRefusalTests(unittest.TestCase):
    def check(self, root, expect, *args, ok=False):
        before = snapshot(root, exclude=())
        r = cli("install", root, *(P + args))
        try:
            if ok:
                self.assertEqual(r.rc, 0, r)
            else:
                self.assertEqual(r.rc, 1, r)
                self.assertEqual(snapshot(root, exclude=()), before)
            self.assertIn(expect, r.out + r.err)
        finally:
            support.cleanup(root)

    def test_untested_model_needs_override(self):
        self.check(support.make_root(model=support.UNTESTED_MODEL), "--allow-untested-hardware")

    def test_untested_model_with_override(self):
        root = support.make_root(model=support.UNTESTED_MODEL)
        self.check(root, "continuing because --allow-untested-hardware", "--allow-untested-hardware", ok=True)

    def test_not_a_pi(self):
        self.check(support.make_root(model="Generic ARM board"), "does not look like a Raspberry Pi")

    def test_untested_os(self):
        self.check(support.make_root(codename="bookworm"), "--allow-untested-os")

    def test_wayland_session(self):
        self.check(support.make_root(session="rpd-labwc"), "Wayland session")

    def test_missing_and_ambiguous_connector(self):
        root = support.make_root()
        import shutil
        shutil.rmtree(os.path.join(root, "sys/class/drm/card1-HDMI-A-1"))
        self.check(root, "DRM connector HDMI-A-1 not found")
        root = support.make_root()
        write(root, "/sys/class/drm/card0-HDMI-A-1/status", "connected\n")
        self.check(root, "is ambiguous")

    def test_unsafe_existing_hook(self):
        root = support.make_root()
        write(root, "/home/pi/hook.sh", "#!/bin/sh\n", 0o755)
        text = read(root, "/etc/lightdm/lightdm.conf").replace("/usr/share/dispsetup.sh", "/home/pi/hook.sh")
        write(root, "/etc/lightdm/lightdm.conf", text)
        self.check(root, "refusing to run it as root")

    def test_replace_display_hook(self):
        root = support.make_root()
        write(root, "/home/pi/hook.sh", "#!/bin/sh\n", 0o755)
        text = read(root, "/etc/lightdm/lightdm.conf").replace("/usr/share/dispsetup.sh", "/home/pi/hook.sh")
        write(root, "/etc/lightdm/lightdm.conf", text)
        r = cli("install", root, *(P + ("--replace-display-hook",)))
        self.assertEqual(r.rc, 0, r)
        self.assertIn("CHAIN=''", read(root, OURS))
        support.cleanup(root)

    def test_seat_specific_override(self):
        root = support.make_root()
        write(root, "/etc/lightdm/lightdm.conf.d/90-x.conf", "[Seat:seat0]\ndisplay-setup-script=/usr/share/dispsetup.sh\n")
        self.check(root, "overrides [Seat:*]")

    def test_overlay_without_noaudio_docs(self):
        root = support.make_root()
        write(root, "/boot/firmware/overlays/README", "Name:   vc4-kms-v3d\nParams: audio x\n")
        self.check(root, "cannot confirm that this OS's vc4-kms-v3d overlay supports 'noaudio'")

    def test_audio_preserve(self):
        root = support.make_root()
        r = cli("install", root, *(P + ("--hdmi-audio=preserve",)))
        self.assertEqual(r.rc, 0, r)
        self.assertEqual(read(root, "/boot/firmware/config.txt"), support.fixture("config.txt.stock"))
        r = cli("install", root, *(P + ("--hdmi-audio=preserve",)))
        self.assertEqual(r.rc, 0, r)
        st = {c["id"]: c for c in json.loads(cli("verify", root, "--json").out)["checks"]}
        self.assertIn("ENABLED", st["audio.policy"]["summary"])
        support.cleanup(root)

    def test_policy_switch_requires_uninstall(self):
        root = support.make_root()
        self.assertEqual(cli("install", root, *P).rc, 0)
        self.check(root, "uninstall first to change the policy", "--hdmi-audio=preserve")


if __name__ == "__main__":
    unittest.main()
