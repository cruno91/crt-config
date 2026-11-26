"""install: preflight checks, plan (dry run) and journaled application."""

import difflib
import os
import shutil
import subprocess
import sys
import tempfile

from . import TOOL_NAME, VERSION
from . import cmdline as cmdline_mod
from . import configtxt, edid, fsutil, lightdm, platform as plat
from .runner import Runner, RunnerError
from .state import State, StateError, new_manifest, now

EDIT_ORDER = ("config_txt", "lightdm", "cmdline")  # cmdline last: it activates the override


class InstallError(Exception):
    pass


class Findings:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.notes = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def note(self, msg):
        self.notes.append(msg)


class FileAction:
    def __init__(self, sys_path, content, mode, what, initramfs_input=False):
        self.sys_path = sys_path
        self.content = content  # bytes, or None for the compiled helper
        self.mode = mode
        self.what = what
        self.initramfs_input = initramfs_input


class EditAction:
    def __init__(self, kind, sys_path, old, new, record, previous):
        self.kind = kind
        self.sys_path = sys_path
        self.old = old
        self.new = new
        self.record = record
        self.previous = previous


class Plan:
    def __init__(self):
        self.missing_packages = []
        self.files = []
        self.edits = []
        self.legacy_moves = []
        self.chained_hook = ""
        self.facts = {}


def expected_print_line(profile):
    return "name=%s clock_khz=%d h=%s v=%s hskew=0 flags=0x%x" % (
        profile.mode_name,
        profile.clock_khz,
        ",".join(str(x) for x in profile.h),
        ",".join(str(x) for x in profile.v),
        profile.mode_flag_bits,
    )


def render_template(path, values):
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    for key, value in values.items():
        text = text.replace("@@%s@@" % key, value)
    if "@@" in text:
        raise InstallError("unrendered placeholder in %s" % path)
    return text.encode("utf-8")


def _sh_quote_args(args):
    """Quote helper arguments for the generated script (values are pre-validated)."""
    out = []
    for a in args:
        if "'" in a:
            raise InstallError("unexpected quote in generated argument %r" % a)
        out.append("'%s'" % a)
    return " ".join(out)


class Installer:
    def __init__(self, repo, root, profile, options, out=None):
        self.repo = repo
        self.root = root
        self.profile = profile
        self.opt = options
        self.out = out or (lambda s="": print(s))
        self.runner = Runner(root)
        self.state = State(root)
        self.f = Findings()
        self.plan = Plan()
        self.manifest = None

    def _read(self, sys_path):
        return self.root.read_text(sys_path)

    def _repo_path(self, *parts):
        return os.path.join(self.repo, *parts)

    def preflight(self):
        p, f, root, opt = self.profile, self.f, self.root, self.opt
        if root.is_live and not opt["dry_run"] and os.geteuid() != 0:
            f.error("must run as root (use sudo), or use --dry-run")

        try:
            self.manifest = self.state.load()
        except StateError as exc:
            f.error(str(exc))
        m = self.manifest
        if m:
            if m.get("profile_id") != p.id:
                f.error("profile %s is installed; uninstall it before installing %s" % (m.get("profile_id"), p.id))
            elif m.get("state") == "uninstall-incomplete":
                f.error("a previous uninstall did not finish; resolve its conflicts and rerun uninstall.sh")
            elif m.get("state") in ("installing", "uninstalling"):
                f.warn("resuming an interrupted %s (state %s)" % ("installation", m.get("state")))
            else:
                f.note("profile already installed; reinstalling idempotently (original baseline is kept)")

        self._check_platform()
        self._check_assets()
        self._check_boot_config()
        self._check_lightdm()
        self._check_connector()
        self._check_legacy()
        self._check_packages()
        return f

    def _check_platform(self):
        p, f, root, opt = self.profile, self.f, self.root, self.opt
        model = plat.read_model(root)
        self.plan.facts["model"] = model
        if not model or not model.startswith("Raspberry Pi"):
            f.error("this does not look like a Raspberry Pi (device-tree model: %r)" % model)
        elif not plat.model_matches(model, p.tested_models):
            msg = "hardware %r is not a tested model for this profile (tested: %s)" % (model, "; ".join(p.tested_models))
            if opt["allow_untested_hardware"]:
                f.warn(msg + "; continuing because --allow-untested-hardware was given")
            else:
                f.error(msg + "; rerun with --allow-untested-hardware to try anyway")

        osr = plat.read_os_release(root)
        self.plan.facts["os"] = osr.get("PRETTY_NAME")
        ids = [osr.get("ID", "")] + osr.get("ID_LIKE", "").split()
        codename = osr.get("VERSION_CODENAME", "")
        if p.os_id not in ids or codename not in p.os_codenames:
            msg = "OS %r (codename %r) is not a tested OS for this profile (tested: %s %s)" % (
                osr.get("PRETTY_NAME"), codename, p.os_id, "/".join(p.os_codenames))
            if opt["allow_untested_os"]:
                f.warn(msg + "; continuing because --allow-untested-os was given")
            else:
                f.error(msg + "; rerun with --allow-untested-os to try anyway")

        arch = self.runner.run(["dpkg", "--print-architecture"])
        arch_name = arch.stdout.strip() if arch.ok else None
        self.plan.facts["arch"] = arch_name
        if arch_name not in p.os_arch:
            msg = "architecture %r is not tested (tested: %s)" % (arch_name, "/".join(p.os_arch))
            if opt["allow_untested_os"]:
                f.warn(msg)
            else:
                f.error(msg + "; rerun with --allow-untested-os to try anyway")

        release = plat.kernel_release(root)
        self.plan.facts["kernel"] = release
        if release != p.kernel_tested:
            f.warn("running kernel %s differs from the tested kernel %s; VC4 behaviour may differ, "
                   "revalidate after installing" % (release, p.kernel_tested))
        if not plat.boot_initramfs_name(release or ""):
            f.warn("cannot tell which firmware initramfs belongs to kernel %r" % release)

    def _check_assets(self):
        p, f = self.profile, self.f
        crt = self._repo_path("edid", p.edid_file)
        src = self._repo_path("edid", p.edid_source_file)
        for path in (crt, src):
            if not os.path.isfile(path):
                f.error("missing EDID asset %s" % path)
                return
        with open(crt, "rb") as fh:
            crt_bytes = fh.read()
        with open(src, "rb") as fh:
            src_bytes = fh.read()
        if fsutil.sha256_bytes(crt_bytes) != p.edid_sha256:
            f.error("%s does not match EDID_SHA256 in the profile" % crt)
        if fsutil.sha256_bytes(src_bytes) != p.edid_source_sha256:
            f.error("%s does not match EDID_SOURCE_SHA256 in the profile" % src)
        try:
            problems = edid.restriction_problems(crt_bytes)
            name = edid.inspect(src_bytes)["monitor_name"]
        except edid.EdidError as exc:
            f.error("EDID asset is invalid: %s" % exc)
            return
        for problem in problems:
            f.error("restricted EDID check failed: %s" % problem)
        if name != p.converter_edid_name:
            f.error("original EDID monitor name %r does not match CONVERTER_EDID_NAME %r" % (name, p.converter_edid_name))
        gen = subprocess.run(
            [sys.executable, "-I", self._repo_path("edid", "generate-edid.py"),
             "--input", src, "--expected", crt, "--check"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
        )
        if gen.returncode != 0:
            f.error("EDID generator does not reproduce the committed EDID: %s" % gen.stdout.strip())
        self.edid_bytes = crt_bytes

    def _check_boot_config(self):
        p, f, root, opt = self.profile, self.f, self.root, self.opt
        model = self.plan.facts.get("model") or ""
        for path in (plat.CMDLINE, plat.CONFIG_TXT):
            if not root.exists(path):
                f.error("%s not found; only Raspberry Pi OS releases using /boot/firmware are supported" % path)
                return
        prev_edits = (self.manifest or {}).get("edits", {})

        cfg = self._read(plat.CONFIG_TXT)
        self.plan.facts["config_txt"] = cfg
        if "1" not in configtxt.find_key(cfg, "disable_fw_kms_setup", model):
            f.warn("config.txt lacks disable_fw_kms_setup=1; the firmware may add its own video= arguments")
        if "1" not in configtxt.find_key(cfg, "auto_initramfs", model) and not configtxt.explicit_initramfs(cfg, model):
            f.warn("config.txt has neither auto_initramfs=1 nor an initramfs line; the firmware may not load the "
                   "initramfs that carries the EDID")
        legacy = configtxt.legacy_video_settings(cfg, model)
        if legacy:
            f.warn("config.txt contains legacy firmware video settings that KMS ignores or that may interfere: %s"
                   % ", ".join(legacy))
        policy = opt["hdmi_audio"]
        try:
            prev = prev_edits.get("config_txt", {}).get("record")
            new_cfg, record = configtxt.plan_audio(cfg, model, policy, previous=prev)
            info = configtxt.find_vc4_overlay(cfg, model)
            self.plan.facts["vc4_overlay"] = info["line"].strip()
            if policy == "disabled" and not info["noaudio"]:
                readme = root.read_text(plat.OVERLAY_README, default="")
                if not configtxt.overlay_documents_noaudio(readme):
                    f.error("cannot confirm that this OS's vc4-kms-v3d overlay supports 'noaudio' (%s does not "
                            "document it); use --hdmi-audio=preserve or check the overlay manually" % plat.OVERLAY_README)
            if policy == "preserve":
                f.note("HDMI audio policy 'preserve': config.txt audio settings are left as they are (%s)"
                       % ("noaudio present" if info["noaudio"] else "HDMI audio enabled"))
                if prev_edits.get("config_txt"):
                    f.error("an earlier installation disabled HDMI audio; uninstall first to change the policy to preserve")
            elif record is None:
                f.note("HDMI audio is already disabled in config.txt (%s); nothing to change" % info["line"].strip())
            if record is not None or prev:
                self.plan.edits.append(EditAction("config_txt", plat.CONFIG_TXT, cfg, new_cfg, record, prev))
        except configtxt.ConfigTxtError as exc:
            f.error("config.txt: %s" % exc)

        text = self._read(plat.CMDLINE)
        self.plan.facts["cmdline"] = text
        try:
            prev = prev_edits.get("cmdline", {}).get("record")
            new_text, record = cmdline_mod.plan_install(
                text, p.drm_connector, p.edid_firmware_path, p.kernel_video_mode, previous=prev)
            if "root=" not in text:
                f.warn("cmdline.txt has no root= argument; it is left untouched, but check it")
            self.plan.edits.append(EditAction("cmdline", plat.CMDLINE, text, new_text, record, prev))
        except cmdline_mod.CmdlineError as exc:
            f.error("cmdline.txt: %s" % exc)

    def _check_lightdm(self):
        f, root, opt = self.f, self.root, self.opt
        if not root.exists(plat.LIGHTDM_BIN):
            f.error("LightDM is not installed; this profile needs Raspberry Pi OS with desktop (X11 + LightDM). "
                    "Lite installations need a desktop stack first, see README")
            return
        dm = root.read_text(plat.DEFAULT_DM, default="").strip()
        if dm and dm != plat.LIGHTDM_BIN:
            f.error("default display manager is %s, not LightDM" % dm)
        elif not dm:
            f.warn("%s is missing; cannot confirm LightDM is the active display manager" % plat.DEFAULT_DM)
        if not (root.exists("/usr/lib/xorg/Xorg") or root.exists("/usr/bin/Xorg")):
            f.error("the X.Org server is not installed; this profile requires X11")
        if not root.exists(lightdm.MAIN_CONF):
            f.error("%s not found" % lightdm.MAIN_CONF)
            return

        files = lightdm.config_files(root)
        eff = lightdm.effective_seat(files)
        shown = self._show_config()
        if shown is not None:
            eff_live = lightdm.effective_from_show_config(shown)
            file_val = eff.get(lightdm.KEY, (None,))[0]
            if eff_live.get(lightdm.KEY) != file_val:
                f.warn("lightdm --show-config reports %s=%r but the file model gives %r; trusting lightdm"
                       % (lightdm.KEY, eff_live.get(lightdm.KEY), file_val))
                eff[lightdm.KEY] = (eff_live.get(lightdm.KEY), "lightdm --show-config", "Seat:*")
        session = (eff.get("autologin-session") or eff.get("user-session") or (None,))[0]
        self.plan.facts["session"] = session
        kind = lightdm.session_kind(root, session) if session else None
        if session is None:
            f.warn("LightDM has no user-session/autologin-session configured; cannot confirm an X11 session")
        elif kind == "wayland":
            f.error("LightDM session %r is a Wayland session; switch to X11 first (Raspberry Pi OS: "
                    "sudo raspi-config, Advanced Options, Wayland, X11) and reboot" % session)
        elif kind is None:
            f.warn("cannot find a session file for %r in /usr/share/xsessions" % session)

        current = eff.get(lightdm.KEY)
        value, source, section = current if current else (None, None, None)
        self.plan.facts["display_setup_current"] = value
        if section and section not in ("Seat:*", "SeatDefaults"):
            f.error("%s is set in [%s] of %s, which overrides [Seat:*]; remove it manually first"
                    % (lightdm.KEY, section, source))
            return
        chained = ""
        if value in (None, "", plat.DISPLAY_SETUP):
            pass
        elif value == plat.LEGACY_DISPLAY_SETUP:
            if opt["migrate_legacy"]:
                f.note("replacing the hand-made display hook %s (left in place, no longer referenced)" % value)
            else:
                f.error("LightDM uses the hand-made hook %s; rerun with --migrate-legacy "
                        "to replace it (the original setting is backed up and restored by uninstall)" % value)
        elif opt["replace_display_hook"]:
            f.warn("replacing existing display-setup-script %s (--replace-display-hook)" % value)
        else:
            ok, why = fsutil.is_safe_root_executable(root.p(value), root) if value.startswith("/") else (False, "is not an absolute path")
            if ok:
                chained = value
                f.note("existing display-setup-script %s will run first, then the CRT setup is applied" % value)
            else:
                f.error("existing display-setup-script %s %s; refusing to run it as root. Fix it or rerun with "
                        "--replace-display-hook" % (value, why))
        prev_record = ((self.manifest or {}).get("edits", {}).get("lightdm") or {}).get("record")
        if prev_record and prev_record.get("chained_hook") is not None and value == plat.DISPLAY_SETUP:
            chained = prev_record.get("chained_hook") or ""
        self.plan.chained_hook = chained

        text = self._read(lightdm.MAIN_CONF)
        try:
            new_text, record = lightdm.plan_install(text, plat.DISPLAY_SETUP, previous=prev_record)
            record["chained_hook"] = chained
            self.plan.edits.append(EditAction("lightdm", lightdm.MAIN_CONF, text, new_text, record, prev_record))
        except lightdm.LightDMError as exc:
            f.error("LightDM: %s" % exc)

    def _show_config(self):
        if not self.root.is_live and not self.runner.which("lightdm"):
            return None
        res = self.runner.run(["lightdm", "--show-config"], timeout=30)
        if not res.ok:
            return None
        return lightdm.parse_show_config(res.stdout + "\n" + res.stderr)

    def _check_connector(self):
        p, f = self.profile, self.f
        dirs = plat.connector_dirs(self.root, p.drm_connector)
        if not dirs:
            f.error("DRM connector %s not found under /sys/class/drm (is the vc4-kms-v3d driver active?)" % p.drm_connector)
            return
        if len(dirs) > 1:
            f.error("DRM connector %s is ambiguous: %s" % (p.drm_connector, ", ".join(os.path.basename(d) for d in dirs)))
            return
        status = ""
        try:
            with open(os.path.join(dirs[0], "status")) as fh:
                status = fh.read().strip()
        except OSError:
            pass
        self.plan.facts["connector"] = "%s (%s)" % (os.path.basename(dirs[0]), status or "unknown")
        if status != "connected":
            f.warn("%s reports status %r; the converter may be off or unplugged (installation can continue)"
                   % (p.drm_connector, status or "unknown"))

    def _check_legacy(self):
        f, opt, root = self.f, self.opt, self.root
        legacy_done = (self.manifest or {}).get("legacy", {})
        if root.exists(plat.LEGACY_INITRAMFS_HOOK) and plat.LEGACY_INITRAMFS_HOOK not in legacy_done:
            if opt["migrate_legacy"]:
                self.plan.legacy_moves.append(plat.LEGACY_INITRAMFS_HOOK)
                f.note("moving the hand-made initramfs hook %s aside (restored by uninstall)" % plat.LEGACY_INITRAMFS_HOOK)
            else:
                f.error("found the hand-made initramfs hook %s (it also references "
                        "converter-480i.bin); rerun with --migrate-legacy to move it aside" % plat.LEGACY_INITRAMFS_HOOK)
        for path in (plat.LEGACY_HELPER, plat.LEGACY_DISPLAY_SETUP):
            if root.exists(path):
                f.note("hand-made file %s is left untouched" % path)

    def _check_packages(self):
        f, opt = self.f, self.opt
        wanted = list(plat.REQUIRED_PACKAGES)
        if opt["with_diagnostics"]:
            wanted += list(plat.DIAGNOSTIC_PACKAGES)
        res = self.runner.run(["dpkg-query", "-W", "-f=${Package} ${Status}\\n"] + wanted)
        installed = set()
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1:4] == ["install", "ok", "installed"]:
                installed.add(parts[0].split(":")[0])
        missing = [pkg for pkg in wanted if pkg not in installed]
        self.plan.missing_packages = missing
        if missing and not opt["install_deps"]:
            f.error("missing packages: %s (rerun without --no-install-deps, or install them)" % " ".join(missing))

    def build_plan(self):
        p, plan = self.profile, self.plan
        helper_args = _sh_quote_args(p.helper_args())
        values = {
            "PROFILE_ID": p.id,
            "XRANDR_OUTPUT": p.xrandr_output,
            "MODE_NAME": p.mode_name,
            "HELPER": plat.HELPER,
            "HELPER_ARGS": helper_args,
            "DISPLAY_SETUP": plat.DISPLAY_SETUP,
            "LOG_FILE": plat.LOG_FILE,
            "CHAINED_HOOK": plan.chained_hook,
            "MARGIN_LEFT": str(p.margin_left),
            "MARGIN_RIGHT": str(p.margin_right),
            "MARGIN_TOP": str(p.margin_top),
            "MARGIN_BOTTOM": str(p.margin_bottom),
        }
        setup = render_template(self._repo_path("templates", "display-setup.sh"), values)
        hook = render_template(self._repo_path("templates", "initramfs-hook.sh"),
                               {"PROFILE_ID": p.id, "EDID_FILES": p.edid_file})
        plan.files = [
            FileAction(plat.HELPER, None, 0o755, "XRandR mode helper (compiled from src/crt-xrandr.c)"),
            FileAction(plat.DISPLAY_SETUP, setup, 0o755, "LightDM display-setup-script"),
            FileAction(plat.EDID_DIR + "/" + p.edid_file, self.edid_bytes, 0o644, "restricted 480i EDID",
                       initramfs_input=True),
            FileAction(plat.INITRAMFS_HOOK, hook, 0o755, "initramfs hook", initramfs_input=True),
        ]
        managed = (self.manifest or {}).get("files", {})
        for copy_path, src_path in ((plat.CMDLINE_BASELINE_COPY, plat.CMDLINE), (plat.CONFIG_BASELINE_COPY, plat.CONFIG_TXT)):
            if copy_path in managed:
                content = self.root.read_bytes(copy_path) if self.root.exists(copy_path) else None
                if content is not None:
                    plan.files.append(FileAction(copy_path, content, 0o644, "boot-partition copy of the original %s" % src_path))
            elif not self.root.exists(copy_path) and self.root.exists(src_path):
                plan.files.append(FileAction(copy_path, self.root.read_bytes(src_path), 0o644,
                                             "boot-partition copy of the original %s (for recovery from another computer)" % src_path))
        return plan

    def print_plan(self):
        out, plan = self.out, self.plan
        out("Plan for profile %s (HDMI audio policy: %s)" % (self.profile.id, self.opt["hdmi_audio"]))
        for key in ("model", "os", "arch", "kernel", "connector", "session", "display_setup_current", "vc4_overlay"):
            if key in plan.facts:
                out("  %-22s %s" % (key + ":", plan.facts[key]))
        out("")
        if plan.missing_packages:
            out("  [packages] apt-get install --no-install-recommends %s" % " ".join(plan.missing_packages))
        out("  [build]    gcc src/crt-xrandr.c -> %s (self-test: --print must match the profile)" % plat.HELPER)
        for fa in plan.files:
            real = self.root.p(fa.sys_path)
            info = fsutil.file_info(real)
            if fa.content is None:
                state = "new" if info is None else "replace"
            elif info is None:
                state = "new"
            elif info.get("sha256") == fsutil.sha256_bytes(fa.content):
                state = "unchanged"
            else:
                state = "replace (current copy is backed up)"
            out("  [file]     %s  %04o  %s - %s" % (fa.sys_path, fa.mode, fa.what, state))
        for path in plan.legacy_moves:
            out("  [legacy]   move %s to %s/legacy/ (restored by uninstall)" % (path, plat.STATE_DIR))
        out("  [initramfs] update-initramfs -u -k all, then confirm %s is inside the boot initramfs" % self.profile.edid_file)
        for ea in sorted(plan.edits, key=lambda e: EDIT_ORDER.index(e.kind)):
            if ea.old == ea.new:
                out("  [edit]     %s - unchanged" % ea.sys_path)
                continue
            out("  [edit]     %s" % ea.sys_path)
            diff = difflib.unified_diff(ea.old.splitlines(), ea.new.splitlines(),
                                        "a" + ea.sys_path, "b" + ea.sys_path, lineterm="", n=1)
            for line in diff:
                out("             " + line)
        if plan.chained_hook:
            out("  [chain]    existing display hook %s runs first, then the CRT mode is applied" % plan.chained_hook)
        out("  [state]    manifest and baseline backups in %s" % plat.STATE_DIR)

    def apply(self):
        with self.state.lock():
            self._install_packages()
            helper_bytes = self._compile_helper()
            for fa in self.plan.files:
                if fa.content is None:
                    fa.content = helper_bytes

            m = self.manifest or new_manifest(self.profile, {}, VERSION)
            m["state"] = "installing"
            m["profile"] = dict(self.profile.raw)
            m["options"] = {"hdmi_audio": self.opt["hdmi_audio"], "migrate_legacy": self.opt["migrate_legacy"]}
            m["tool_version"] = VERSION
            m["runs"].append({"action": "install", "started": now(), "stamp": self.state.run_stamp})
            self.manifest = m
            self.state.save(m)

            initramfs_changed = False
            for fa in self.plan.files:
                changed = self._install_file(fa)
                initramfs_changed = initramfs_changed or (changed and fa.initramfs_input)
            for path in self.plan.legacy_moves:
                self._move_legacy(path)
                initramfs_changed = True
            self._update_initramfs(force=initramfs_changed)

            for kind in EDIT_ORDER:
                for ea in self.plan.edits:
                    if ea.kind == kind:
                        self._apply_edit(ea)

            m["state"] = "installed"
            m["runs"][-1]["finished"] = now()
            m["runs"][-1]["result"] = "ok"
            self.state.save(m)

    def _install_packages(self):
        pkgs = self.plan.missing_packages
        if not pkgs:
            return
        self.out("Installing packages: %s" % " ".join(pkgs))
        res = self.runner.run(["apt-get", "install", "-y", "--no-install-recommends"] + pkgs,
                              env={"DEBIAN_FRONTEND": "noninteractive"}, timeout=3600)
        if not res.ok:
            raise InstallError("apt-get install failed (no system files were changed yet). Run 'sudo apt update' "
                               "and retry.\n%s" % (res.stderr or res.stdout)[-2000:])

    def _compile_helper(self):
        self.state.ensure_dir()
        build = tempfile.mkdtemp(prefix="build.", dir=self.state.dir)
        try:
            pc = self.runner.run(["pkg-config", "--cflags", "--libs", "xrandr", "x11"], check=True)
            out = os.path.join(build, "crt-xrandr")
            self.runner.run(["gcc", "-Wall", "-Wextra", "-O2", "-o", out,
                             self._repo_path("src", "crt-xrandr.c")] + pc.stdout.split(), check=True)
            test = subprocess.run([out] + self.profile.helper_args() + ["--print"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                                  env={"PATH": "/usr/bin:/bin"}, timeout=30)
            want = expected_print_line(self.profile)
            if test.returncode != 0 or test.stdout.strip() != want:
                raise InstallError("compiled helper self-test failed:\n  expected: %s\n  got:      %s %s"
                                   % (want, test.stdout.strip(), test.stderr.strip()))
            with open(out, "rb") as fh:
                return fh.read()
        except RunnerError as exc:
            raise InstallError("building crt-xrandr failed (no system files were changed yet): %s" % exc)
        finally:
            shutil.rmtree(build, ignore_errors=True)

    def _owner(self):
        return (0, 0) if self.root.is_live else None

    def _install_file(self, fa):
        m = self.manifest
        real = self.root.p(fa.sys_path)
        want_sha = fsutil.sha256_bytes(fa.content)
        entry = m["files"].get(fa.sys_path)
        if entry is None:
            entry = {"prior": self.state.capture_prior(fa.sys_path)}
            m["files"][fa.sys_path] = entry
        entry.update({"sha256": want_sha, "mode": "%04o" % fa.mode, "status": "pending"})
        self.state.save(m)

        cur = fsutil.file_info(real)
        # /boot/firmware is vfat: modes and owners come from mount options
        # (typically 0755 root:root) and cannot be changed per file.
        on_vfat = fa.sys_path.startswith(plat.BOOT_DIR + "/")
        same = cur is not None and cur.get("sha256") == want_sha and (on_vfat or cur.get("mode") == "%04o" % fa.mode)
        if same and self.root.is_live and not on_vfat:
            same = cur.get("uid") == 0 and cur.get("gid") == 0
        if not same:
            self.state.run_backup(fa.sys_path)
            created = fsutil.makedirs(os.path.dirname(real), mode=0o755)
            for d in created:
                sys_d = d if self.root.is_live else "/" + os.path.relpath(d, self.root.root)
                if sys_d not in m["dirs_created"]:
                    m["dirs_created"].append(sys_d)
            fsutil.atomic_write(real, fa.content, mode=fa.mode, owner=self._owner())
            self.out("  installed %s" % fa.sys_path)
        entry["mode"] = fsutil.file_info(real)["mode"]  # what the filesystem actually reports
        entry["status"] = "applied"
        self.state.save(m)
        return not same

    def _move_legacy(self, sys_path):
        m = self.manifest
        info = fsutil.file_info(self.root.p(sys_path))
        rel = self.state.move_aside(sys_path)
        m["legacy"][sys_path] = {"moved_to": rel, "sha256": info["sha256"], "mode": info["mode"]}
        self.state.save(m)
        self.out("  moved %s aside" % sys_path)

    def _apply_edit(self, ea):
        m = self.manifest
        entry = m["edits"].get(ea.kind)
        if entry is None:
            entry = {"path": ea.sys_path, "baseline": self.state.capture_prior(ea.sys_path)}
            m["edits"][ea.kind] = entry
        if ea.record is None:
            m["edits"].pop(ea.kind, None)
            self.state.save(m)
            return
        entry.update({"record": ea.record, "status": "pending"})
        self.state.save(m)
        real = self.root.p(ea.sys_path)
        current = self.root.read_text(ea.sys_path)
        if current != ea.old:
            raise InstallError("%s changed while installing; rerun install.sh" % ea.sys_path)
        if ea.new != ea.old:
            self.state.run_backup(ea.sys_path)
            fsutil.atomic_write(real, ea.new, like=real)
            self.out("  edited %s" % ea.sys_path)
        if ea.kind == "lightdm":
            self._confirm_lightdm(ea)
        entry["status"] = "applied"
        self.state.save(m)

    def _confirm_lightdm(self, ea):
        shown = self._show_config()
        if shown is None:
            files = lightdm.config_files(self.root)
            value = (lightdm.effective_seat(files).get(lightdm.KEY) or (None,))[0]
            source = "configuration file model"
        else:
            value = lightdm.effective_from_show_config(shown).get(lightdm.KEY)
            source = "lightdm --show-config"
        if value != plat.DISPLAY_SETUP:
            fsutil.atomic_write(self.root.p(ea.sys_path), ea.old, like=self.root.p(ea.sys_path))
            self.manifest["edits"].pop("lightdm", None)
            self.state.save(self.manifest)
            raise InstallError("after editing %s, %s reports %s=%r instead of %s; the edit was reverted. "
                               "Another LightDM configuration source overrides [Seat:*]."
                               % (ea.sys_path, source, lightdm.KEY, value, plat.DISPLAY_SETUP))

    def _update_initramfs(self, force):
        p = self.profile
        boot_name = plat.boot_initramfs_name(plat.kernel_release(self.root) or "")
        cfg = self.root.read_text(plat.CONFIG_TXT, default="")
        explicit = configtxt.explicit_initramfs(cfg, self.plan.facts.get("model") or "")
        boot_file = plat.BOOT_DIR + "/" + (explicit or boot_name or "initramfs8")
        if not force and contains_edid(self.runner, self.root, boot_file, p.edid_file):
            self.out("  initramfs %s already contains %s; not rebuilding" % (boot_file, p.edid_file))
            self.manifest["initramfs"] = {"boot_file": boot_file, "checked": now(), "contains_edid": True}
            return
        self.out("  rebuilding initramfs (update-initramfs -u -k all) ...")
        res = self.runner.run(["update-initramfs", "-u", "-k", "all"], timeout=1800)
        if not res.ok:
            raise InstallError("update-initramfs failed; the boot configuration has NOT been changed yet. "
                               "Fix the error and rerun install.sh, or run uninstall.sh.\n%s"
                               % (res.stderr or res.stdout)[-2000:])
        if not self.root.exists(boot_file):
            raise InstallError("boot initramfs %s does not exist after update-initramfs; the boot configuration "
                               "has NOT been changed. Check auto_initramfs in config.txt." % boot_file)
        if not contains_edid(self.runner, self.root, boot_file, p.edid_file):
            raise InstallError("%s does not contain %s after rebuilding; the boot configuration has NOT been "
                               "changed" % (boot_file, p.edid_file))
        self.manifest["initramfs"] = {"boot_file": boot_file, "rebuilt": now(), "contains_edid": True}
        self.state.save(self.manifest)
        self.out("  confirmed %s contains %s" % (boot_file, p.edid_file))


def contains_edid(runner, root, boot_file, edid_file):
    if not root.exists(boot_file):
        return False
    res = runner.run(["lsinitramfs", root.p(boot_file)], timeout=300)
    if not res.ok:
        return False
    targets = ("lib/firmware/edid/" + edid_file, "usr/lib/firmware/edid/" + edid_file)
    return any(line.strip().lstrip("./").endswith(targets) for line in res.stdout.splitlines())
