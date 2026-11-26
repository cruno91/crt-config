"""verify: read-only checks of files *and* runtime state.

Statuses: PASS, FAIL, WARN, PENDING (correctly configured but needs a reboot
to take effect) and NOT_TESTED (could not be checked here, e.g. no X access
or a physical check only the owner can do).

Exit code: 0 if every required check passed, 1 if any check FAILed,
2 if nothing failed but some required checks are PENDING or NOT_TESTED.

Nothing here writes to the system, plays audio, or changes display modes.
"""

import json
import os
import re

from . import cmdline as cmdline_mod
from . import configtxt, edid, fsutil, lightdm, parsers, platform as plat
from . import profile as profile_mod
from .installer import contains_edid, expected_print_line
from .runner import Runner
from .state import State, StateError

PASS, FAIL, WARN, PENDING, NOT_TESTED = "PASS", "FAIL", "WARN", "PENDING", "NOT_TESTED"


class Check:
    def __init__(self, cid, status, required, summary, detail=None):
        self.id = cid
        self.status = status
        self.required = required
        self.summary = summary
        self.detail = detail or []

    def as_dict(self):
        return {"id": self.id, "status": self.status, "required": self.required,
                "summary": self.summary, "detail": self.detail}


class Verifier:
    def __init__(self, repo, root, profile_name=None, runtime_only=False,
                 display=None, xauthority=None):
        self.repo = repo
        self.root = root
        self.profile_name = profile_name
        self.runtime_only = runtime_only
        self.display = display
        self.xauthority = xauthority
        self.runner = Runner(root)
        self.checks = []
        self.manifest = None
        self.profile = None
        self.model = ""

    def add(self, cid, status, required, summary, detail=None):
        self.checks.append(Check(cid, status, required, summary, detail))

    def run(self):
        self._load_profile()
        if self.profile is None:
            return self.checks
        self._platform()
        self._install_state()
        self._lightdm()
        self._edid()
        self._initramfs()
        self._cmdline()
        self._audio()
        self._x11()
        self._drm()
        self._persistence()
        self._manual()
        return self.checks

    def _load_profile(self):
        try:
            self.manifest = State(self.root).load()
        except StateError as exc:
            self.add("install.manifest", FAIL, True, str(exc))
        repo_profile = None
        if self.profile_name:
            try:
                repo_profile = profile_mod.load(self.repo, self.profile_name)
            except profile_mod.ProfileError as exc:
                self.add("profile", FAIL, True, str(exc))
                return
        if self.manifest and not self.runtime_only:
            if repo_profile and self.manifest.get("profile_id") != repo_profile.id:
                self.add("profile", FAIL, True, "installed profile is %s, not %s"
                         % (self.manifest.get("profile_id"), repo_profile.id))
                return
            try:
                self.profile = profile_mod.from_raw(self.manifest["profile"])
            except profile_mod.ProfileError as exc:
                self.add("profile", FAIL, True, "installed profile in manifest is invalid: %s" % exc)
                return
            if repo_profile and repo_profile.raw != self.profile.raw:
                changed = sorted(k for k in set(repo_profile.raw) | set(self.profile.raw)
                                 if repo_profile.raw.get(k) != self.profile.raw.get(k))
                self.add("install.profile-drift", WARN, False,
                         "profiles/%s.conf changed since installation; rerun install.sh to apply" % repo_profile.id,
                         ["changed keys: " + ", ".join(changed)])
        else:
            if repo_profile is None:
                self.add("profile", FAIL, True, "no installation found; pass --profile to check against a profile")
                return
            self.profile = repo_profile

    def _boot_state(self):
        """'booted' if the running kernel has the profile arguments, 'pending' if only
        cmdline.txt has them (reboot needed), otherwise 'absent'."""
        p = self.profile
        want = (p.edid_firmware_path, "video=%s:%s" % (p.drm_connector, p.kernel_video_mode))

        def has(text):
            try:
                st = cmdline_mod.current_state(text.split(), p.drm_connector)
            except cmdline_mod.CmdlineError:
                return False
            return (st["edid"], st["video"]) == want

        if has(self.root.read_text(plat.PROC_CMDLINE, default="")):
            return "booted"
        if has(self.root.read_text(plat.CMDLINE, default="")):
            return "pending"
        return "absent"

    def _policy(self):
        if self.manifest and not self.runtime_only:
            return self.manifest.get("options", {}).get("hdmi_audio", self.profile.hdmi_audio_default)
        return self.profile.hdmi_audio_default

    def _platform(self):
        p = self.profile
        self.model = plat.read_model(self.root) or ""
        if not self.model.startswith("Raspberry Pi"):
            self.add("platform.model", FAIL, True, "not a Raspberry Pi (model %r)" % self.model)
        elif plat.model_matches(self.model, p.tested_models):
            self.add("platform.model", PASS, True, "%s (tested model)" % self.model)
        else:
            self.add("platform.model", WARN, True, "%s is not a tested model (tested: %s)"
                     % (self.model, "; ".join(p.tested_models)))
        osr = plat.read_os_release(self.root)
        ok = p.os_id in [osr.get("ID", "")] + osr.get("ID_LIKE", "").split() and \
            osr.get("VERSION_CODENAME") in p.os_codenames
        self.add("platform.os", PASS if ok else WARN, True, "%s%s" % (osr.get("PRETTY_NAME"), "" if ok else " (untested)"))
        release = plat.kernel_release(self.root)
        self.add("platform.kernel", PASS if release == p.kernel_tested else WARN, False,
                 "kernel %s%s" % (release, "" if release == p.kernel_tested
                                  else " differs from tested %s; revalidate VC4 behaviour" % p.kernel_tested))

    def _install_state(self):
        m, p = self.manifest, self.profile
        if self.runtime_only:
            self.add("install.manifest", NOT_TESTED, False, "skipped (--runtime-only)")
            return
        if not m:
            self.add("install.manifest", FAIL, True, "rpi-crt-profiles is not installed (no manifest in %s)" % plat.STATE_DIR,
                     ["use --runtime-only to check a hand-made setup read-only"])
            return
        if m.get("state") != "installed":
            self.add("install.manifest", FAIL, True, "installation state is %r" % m.get("state"))
        else:
            self.add("install.manifest", PASS, True, "profile %s installed (HDMI audio policy %s)"
                     % (m.get("profile_id"), self._policy()))
        problems = []
        for path, entry in sorted(m.get("files", {}).items()):
            real = self.root.p(path)
            info = fsutil.file_info(real)
            if info is None or info.get("type") != "file":
                problems.append("%s is missing" % path)
                continue
            if info["sha256"] != entry.get("sha256"):
                problems.append("%s content differs from the installed version" % path)
            if info["mode"] != entry.get("mode"):
                problems.append("%s mode %s (expected %s)" % (path, info["mode"], entry.get("mode")))
            if self.root.is_live and (info["uid"], info["gid"]) != (0, 0) and not path.startswith(plat.BOOT_DIR):
                problems.append("%s is not owned by root" % path)
            if int(info["mode"], 8) & 0o022:
                problems.append("%s is group/other writable" % path)
        self.add("install.files", FAIL if problems else PASS, True,
                 "%d installed files intact" % len(m.get("files", {})) if not problems else "installed files changed",
                 problems)
        bproblems = []
        entries = [(p_, e["prior"]) for p_, e in m.get("files", {}).items()]
        entries += [(e["path"], e["baseline"]) for e in m.get("edits", {}).values() if "baseline" in e]
        for path, prior in entries:
            if prior.get("absent"):
                continue
            b = State(self.root).backup_path(prior["backup"])
            if not os.path.isfile(b):
                bproblems.append("baseline backup of %s is missing (%s)" % (path, b))
            elif fsutil.sha256_file(b) != prior["sha256"]:
                bproblems.append("baseline backup of %s does not match its recorded checksum" % path)
        self.add("install.baseline", FAIL if bproblems else PASS, True,
                 "original baseline backups intact" if not bproblems else "baseline backups damaged", bproblems)
        helper = self.root.p(plat.HELPER)
        if os.path.isfile(helper):
            res = self.runner.run([helper] + p.helper_args() + ["--print"]) if not self.root.is_live else \
                self._run_abs([helper] + p.helper_args() + ["--print"])
            want = expected_print_line(p)
            ok = res.ok and res.stdout.strip() == want
            self.add("install.helper", PASS if ok else FAIL, True,
                     "crt-xrandr encodes the profile mode (flags 0x%x)" % p.mode_flag_bits if ok
                     else "crt-xrandr --print does not match the profile",
                     [] if ok else ["expected: " + want, "got: " + (res.stdout or res.stderr).strip()])

    def _run_abs(self, argv, env=None):
        """Run an absolute-path root-owned binary (live system only)."""
        import subprocess
        try:
            proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                                  env=dict({"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}, **(env or {})), timeout=20)
            from .runner import Result
            return Result(argv, proc.returncode, proc.stdout, proc.stderr)
        except Exception as exc:  # timeout or exec failure
            from .runner import Result
            return Result(argv, 1, "", str(exc))

    def _lightdm(self):
        root = self.root
        if not root.exists(lightdm.MAIN_CONF):
            self.add("lightdm.hook", FAIL, True, "%s not found" % lightdm.MAIN_CONF)
            return
        files = lightdm.config_files(root)
        eff = lightdm.effective_seat(files)
        value = (eff.get(lightdm.KEY) or (None,))[0]
        source = "configuration files"
        res = self.runner.run(["lightdm", "--show-config"], timeout=30) if self.runner.which("lightdm") else None
        if res is not None and res.ok:
            shown = lightdm.effective_from_show_config(lightdm.parse_show_config(res.stdout + "\n" + res.stderr))
            value = shown.get(lightdm.KEY)
            source = "lightdm --show-config"
        if value == plat.DISPLAY_SETUP:
            ok, why = fsutil.is_safe_root_executable(root.p(plat.DISPLAY_SETUP), root)
            self.add("lightdm.hook", PASS if ok else FAIL, True,
                     "effective display-setup-script is %s (%s)" % (value, source) if ok
                     else "%s %s" % (plat.DISPLAY_SETUP, why))
        elif self.runtime_only and value:
            ok, why = fsutil.is_safe_root_executable(root.p(value), root)
            status = WARN if ok else FAIL
            self.add("lightdm.hook", status, True,
                     "effective display-setup-script is %s (%s); not managed by rpi-crt-profiles%s"
                     % (value, source, "" if ok else "; it " + why))
        else:
            self.add("lightdm.hook", FAIL, True, "effective display-setup-script is %r (%s), expected %s"
                     % (value, source, plat.DISPLAY_SETUP))
        text = root.read_text(lightdm.MAIN_CONF)
        count = 0
        section = None
        for line in text.splitlines():
            m = lightdm.SECTION_RE.match(line)
            if m:
                section = m.group(1).strip()
            elif section == "Seat:*" and re.match(r"^\s*%s\s*=" % lightdm.KEY, line):
                count += 1
        self.add("lightdm.no-duplicates", PASS if count <= 1 else FAIL, True,
                 "[Seat:*] in %s sets %s %d time(s)" % (lightdm.MAIN_CONF, lightdm.KEY, count))
        session = (eff.get("autologin-session") or eff.get("user-session") or (None,))[0]
        kind = lightdm.session_kind(root, session) if session else None
        self.add("lightdm.session", PASS if kind in ("x11", "both") else (FAIL if kind == "wayland" else WARN), True,
                 "session %r is %s" % (session, kind or "unknown"))

    def _edid(self):
        p = self.profile
        path = plat.EDID_DIR + "/" + p.edid_file
        real = self.root.p(path)
        if not os.path.isfile(real):
            self.add("edid.file", FAIL, True, "%s is missing" % path)
            installed = None
        else:
            with open(real, "rb") as fh:
                installed = fh.read()
            problems = []
            if fsutil.sha256_bytes(installed) != p.edid_sha256:
                problems.append("SHA-256 %s does not match the profile (%s)" % (fsutil.sha256_bytes(installed), p.edid_sha256))
            try:
                problems += edid.restriction_problems(installed)
            except edid.EdidError as exc:
                problems.append(str(exc))
            self.add("edid.file", FAIL if problems else PASS, True,
                     "%s matches the profile, checksums valid, only VIC 6 advertised" % path if not problems
                     else "%s is not the expected restricted EDID" % path, problems)
            decoder = self.runner.which("edid-decode")
            if decoder:
                res = self.runner.run(["edid-decode", "--check", real])
                fails = _edid_decode_failures(res.stdout)
                self.add("edid.decode", PASS, False,
                         "edid-decode --check: %d conformance failure(s); intentional for this EDID, see docs/edid.md"
                         % len(fails), fails)
        dirs = plat.connector_dirs(self.root, p.drm_connector)
        if len(dirs) != 1:
            self.add("edid.active", FAIL, True, "expected exactly one DRM connector %s, found %d" % (p.drm_connector, len(dirs)))
            self._connector_dir = None
            return
        self._connector_dir = dirs[0]
        try:
            with open(os.path.join(dirs[0], "edid"), "rb") as fh:
                active = fh.read()
        except OSError:
            active = b""
        boot = self._boot_state()
        if boot == "pending":
            self.add("edid.active", PENDING, True, "cmdline.txt has the EDID override but the running kernel predates it; reboot")
        elif boot == "absent":
            self.add("edid.active", FAIL, True, "the kernel command line does not carry the EDID override")
        elif not active:
            self.add("edid.active", NOT_TESTED, True, "%s exposes no EDID (converter off or unplugged?)"
                     % os.path.basename(dirs[0]))
        elif installed is not None and active == installed:
            self.add("edid.active", PASS, True, "%s uses the installed EDID" % os.path.basename(dirs[0]))
        else:
            self.add("edid.active", FAIL, True, "%s exposes a different EDID (sha256 %s)"
                     % (os.path.basename(dirs[0]), fsutil.sha256_bytes(active)))

    def _initramfs(self):
        p = self.profile
        cfg = self.root.read_text(plat.CONFIG_TXT, default="")
        release = plat.kernel_release(self.root)
        name = configtxt.explicit_initramfs(cfg, self.model) or plat.boot_initramfs_name(release or "")
        if not name:
            self.add("initramfs.boot", NOT_TESTED, True, "cannot determine the boot initramfs for kernel %r" % release)
        elif not self.runner.which("lsinitramfs"):
            self.add("initramfs.boot", NOT_TESTED, True, "lsinitramfs is not available")
        else:
            boot = plat.BOOT_DIR + "/" + name
            if not self.root.exists(boot):
                self.add("initramfs.boot", FAIL, True, "%s does not exist" % boot)
            elif contains_edid(self.runner, self.root, boot, p.edid_file):
                self.add("initramfs.boot", PASS, True, "%s contains %s" % (boot, p.edid_file))
            else:
                self.add("initramfs.boot", FAIL, True, "%s does not contain %s" % (boot, p.edid_file),
                         ["rebuild with: sudo update-initramfs -u -k all"])
        detail = []
        status = PASS
        hook = self.root.p(plat.INITRAMFS_HOOK)
        legacy = self.root.p(plat.LEGACY_INITRAMFS_HOOK)
        if os.path.isfile(hook):
            ok, why = fsutil.is_safe_root_executable(hook, self.root)
            if not ok:
                status = FAIL
                detail.append("%s %s" % (plat.INITRAMFS_HOOK, why))
        elif not self.runtime_only:
            status = FAIL
            detail.append("%s is missing" % plat.INITRAMFS_HOOK)
        if os.path.isfile(legacy):
            text = self.root.read_text(plat.LEGACY_INITRAMFS_HOOK)
            missing = [f for f in re.findall(r"([A-Za-z0-9._-]+\.bin)", text)
                       if not self.root.exists(plat.EDID_DIR + "/" + f)]
            if missing:
                status = FAIL
                detail.append("hand-made hook %s copies missing firmware %s; update-initramfs will fail"
                              % (plat.LEGACY_INITRAMFS_HOOK, ", ".join(sorted(set(missing)))))
            elif not self.runtime_only:
                status = WARN if status == PASS else status
                detail.append("hand-made hook %s is also present" % plat.LEGACY_INITRAMFS_HOOK)
        self.add("initramfs.hooks", status, True, "initramfs hooks are consistent" if status == PASS
                 else "initramfs hook problems", detail)

    def _cmdline(self):
        p = self.profile
        want_edid = p.edid_firmware_path
        want_video = "video=%s:%s" % (p.drm_connector, p.kernel_video_mode)
        try:
            tokens, _ = cmdline_mod.split(self.root.read_text(plat.CMDLINE))
            state = cmdline_mod.current_state(tokens, p.drm_connector)
            counts = cmdline_mod.duplicates(tokens, p.drm_connector)
        except (cmdline_mod.CmdlineError, FileNotFoundError) as exc:
            self.add("cmdline.file", FAIL, True, "cmdline.txt: %s" % exc)
            return
        problems = []
        if state["edid"] != want_edid:
            problems.append("drm.edid_firmware entry for %s is %r, expected %r" % (p.drm_connector, state["edid"], want_edid))
        if state["video"] != want_video:
            problems.append("video argument is %r, expected %r" % (state["video"], want_video))
        if counts["edid_args"] > 1 or counts["video_args"] > 1:
            problems.append("duplicate owned arguments: %s" % counts)
        if not any(t.startswith("root=") for t in tokens):
            problems.append("no root= argument")
        self.add("cmdline.file", FAIL if problems else PASS, True,
                 "cmdline.txt is one line with %s and %s exactly once" % (want_edid, want_video) if not problems
                 else "cmdline.txt does not carry the profile arguments", problems)
        running = self.root.read_text(plat.PROC_CMDLINE, default="").split()
        have = cmdline_mod.current_state(running, p.drm_connector) if running else {"edid": None, "video": None}
        if have["edid"] == want_edid and have["video"] == want_video:
            self.add("cmdline.running", PASS, True, "running kernel was booted with the profile arguments")
        elif not problems:
            self.add("cmdline.running", PENDING, True, "cmdline.txt is correct but the running kernel predates it; reboot")
        else:
            self.add("cmdline.running", FAIL, True, "running kernel lacks the profile arguments")

    def _audio(self):
        policy = self._policy()
        cfg = self.root.read_text(plat.CONFIG_TXT, default="")
        try:
            info = configtxt.find_vc4_overlay(cfg, self.model)
        except configtxt.ConfigTxtError as exc:
            self.add("audio.policy", FAIL, True, "config.txt: %s" % exc)
            return
        cards = plat.asound_cards(self.root)
        hdmi_cards = plat.hdmi_audio_cards(cards)
        card_list = ["%d [%s] %s" % (c[0], c[1], c[3]) for c in cards] or ["no ALSA cards listed"]
        note = "Disabling is the proposed fix; it is not a physically verified cure for the video corruption."
        if policy == "disabled":
            if not info["noaudio"]:
                self.add("audio.policy", FAIL, True, "policy is 'disabled' but %r lacks noaudio" % info["line"].strip(), card_list)
            elif hdmi_cards:
                self.add("audio.policy", PENDING, True, "noaudio is configured but HDMI ALSA devices still exist; reboot",
                         card_list + [note])
            else:
                self.add("audio.policy", PASS, True,
                         "HDMI audio disabled (%s; no vc4-hdmi ALSA device). No audio was played." % info["line"].strip(),
                         card_list + [note])
        else:
            self.add("audio.policy", PASS, True, "policy 'preserve': HDMI audio %s (%s)"
                     % ("disabled" if info["noaudio"] else "ENABLED - known to corrupt video on this setup",
                        info["line"].strip()), card_list)
        if "1" in configtxt.find_key(cfg, "disable_fw_kms_setup", self.model):
            self.add("config.kms", PASS, False, "disable_fw_kms_setup=1 present")
        else:
            self.add("config.kms", WARN, False, "disable_fw_kms_setup=1 missing; firmware may add video= arguments")

    def _x_env(self):
        env = {"DISPLAY": self.display or os.environ.get("DISPLAY") or ":0"}
        xa = self.xauthority or os.environ.get("XAUTHORITY")
        if not xa and os.geteuid() == 0 and self.root.exists(plat.LIGHTDM_XAUTH):
            xa = self.root.p(plat.LIGHTDM_XAUTH)
        if xa:
            env["XAUTHORITY"] = xa
        return env

    def _x11(self):
        p = self.profile
        env = self._x_env()
        res = self.runner.run(["xrandr", "--verbose"], env=env, timeout=20)
        if not res.ok:
            msg = "cannot query X (DISPLAY=%s XAUTHORITY=%s): %s" % (env.get("DISPLAY"), env.get("XAUTHORITY"),
                                                                      (res.stderr or res.stdout).strip()[:200])
            for cid in ("x11.mode", "x11.margins", "x11.doubleclock", "x11.fallback"):
                self.add(cid, NOT_TESTED, True, msg if cid == "x11.mode" else "skipped: no X access")
            return
        outputs = parsers.parse_xrandr_verbose(res.stdout)
        out = outputs.get(p.xrandr_output)
        if not out or not out["connected"]:
            self.add("x11.mode", FAIL, True, "output %s is %s" % (p.xrandr_output, "missing" if not out else "disconnected"))
            for cid in ("x11.margins", "x11.doubleclock", "x11.fallback"):
                self.add(cid, NOT_TESTED, True, "skipped: output not connected")
            return
        cur = [m for m in out["modes"] if m["current"]]
        problems = []
        if not cur:
            problems.append("no current mode")
        else:
            c = cur[0]
            if c["name"] != p.mode_name:
                problems.append("current mode is %s, expected %s" % (c["name"], p.mode_name))
            if c.get("h") != p.h:
                problems.append("H timings %s, expected %s" % (c.get("h"), p.h))
            if c.get("v") != p.v:
                problems.append("V timings %s, expected %s" % (c.get("v"), p.v))
            if abs(c["clock_mhz"] - p.clock_khz / 1000.0) > 0.0005:
                problems.append("clock %.3f MHz, expected %.3f" % (c["clock_mhz"], p.clock_khz / 1000.0))
            want_flags = []
            want_flags += ["-HSync"] if "nhsync" in p.flags else (["+HSync"] if "phsync" in p.flags else [])
            want_flags += ["-VSync"] if "nvsync" in p.flags else (["+VSync"] if "pvsync" in p.flags else [])
            want_flags += ["Interlace"] if "interlace" in p.flags else []
            if sorted(f for f in c["flags"] if f in ("-HSync", "+HSync", "-VSync", "+VSync", "Interlace")) != sorted(want_flags):
                problems.append("flags %s, expected %s" % (" ".join(c["flags"]), " ".join(want_flags)))
        self.add("x11.mode", FAIL if problems else PASS, True,
                 "%s is active on %s with %s H %s V %s" % (p.mode_name, p.xrandr_output,
                                                         "%.3f MHz" % (p.clock_khz / 1000.0), " ".join(map(str, p.h)),
                                                         " ".join(map(str, p.v))) if not problems
                 else "%s is not active with the expected timings" % p.mode_name, problems)

        margins = {k: out["props"].get("%s margin" % k) for k in ("left", "right", "top", "bottom")}
        want = {k: str(v) for k, v in p.margins.items()}
        self.add("x11.margins", PASS if margins == want else FAIL, True,
                 "margins L/R/T/B = %s/%s/%s/%s" % (margins["left"], margins["right"], margins["top"], margins["bottom"])
                 + ("" if margins == want else " (expected %s/%s/%s/%s)" % (want["left"], want["right"], want["top"], want["bottom"])))

        helper = self.root.p(plat.HELPER)
        if os.path.isfile(helper):
            argv = [helper] + p.helper_args() + ["--query", "--require-active"]
            q = self._run_abs(argv, env) if self.root.is_live else self.runner.run(argv, env=env)
            self.add("x11.doubleclock", PASS if q.ok else FAIL, True,
                     "X mode %s carries flags 0x%x incl. DoubleClock and is active" % (p.mode_name, p.mode_flag_bits)
                     if q.ok else "crt-xrandr --query failed (exit %d)" % q.returncode, [q.stdout.strip() or q.stderr.strip()])
        else:
            self.add("x11.doubleclock", NOT_TESTED, True,
                     "crt-xrandr not installed at %s; xrandr(1) cannot show DoubleClock, rely on drm.active" % plat.HELPER)

        std = [m for m in out["modes"] if m["name"] != p.mode_name]
        fallback = [m for m in std if m.get("h") == (720, 739, 801, 858) and m.get("v") == (480, 488, 494, 525)
                    and "Interlace" in m["flags"]]
        unsafe = [m for m in std if "Interlace" not in m["flags"] or (m.get("v") or (0,))[0] > 480]
        detail = ["%s %s %s" % (m["name"], m.get("h"), " ".join(m["flags"])) for m in out["modes"]]
        if unsafe:
            self.add("x11.fallback", FAIL, True, "non-480i modes are exposed: %s"
                     % ", ".join(m["name"] for m in unsafe), detail)
        elif not fallback:
            self.add("x11.fallback", FAIL, True, "standard 720x480i fallback mode is not available", detail)
        else:
            self.add("x11.fallback", PASS, True, "standard 480i fallback available; no progressive or high-resolution modes", detail)

    def _drm(self):
        p = self.profile
        base = self.root.p(plat.DEBUGFS_DRI)
        if self.root.is_live and os.geteuid() != 0:
            self.add("drm.active", NOT_TESTED, True, "run as root to read %s" % plat.DEBUGFS_DRI)
        elif not os.path.isdir(base):
            self.add("drm.active", NOT_TESTED, True, "%s is not available (debugfs not mounted?)" % plat.DEBUGFS_DRI)
        else:
            # Several debugfs entries (dri/0, dri/1, by-name links) can describe
            # the same device. Group identical answers so duplicates are not
            # mistaken for separate displays; disagreement is a failure.
            seen = set()
            groups = {}
            readable = 0
            for entry in sorted(os.listdir(base)):
                f = os.path.join(base, entry, "state")
                real = os.path.realpath(os.path.join(base, entry))
                if not os.path.isfile(f) or real in seen:
                    continue
                seen.add(real)
                try:
                    with open(f, "r", errors="replace") as fh:
                        st = parsers.parse_drm_state(fh.read())
                except OSError:
                    continue
                readable += 1
                if p.drm_connector not in st["connectors"]:
                    continue
                crtc, why = parsers.active_crtc_for(st, p.drm_connector)
                key = ((crtc or {}).get("name"), repr((crtc or {}).get("mode")), why)
                groups.setdefault(key, {"crtc": crtc, "why": why, "entries": []})["entries"].append(entry)
            if not readable:
                self.add("drm.active", NOT_TESTED, True, "no readable DRM state under %s" % plat.DEBUGFS_DRI)
            elif not groups:
                self.add("drm.active", FAIL, True, "no DRM state lists connector %s" % p.drm_connector)
            elif len(groups) > 1:
                self.add("drm.active", FAIL, True, "DRM state views disagree about %s" % p.drm_connector,
                         ["%s: %s" % (",".join(g["entries"]), k) for k, g in groups.items()])
            else:
                g = next(iter(groups.values()))
                crtc, why, entries = g["crtc"], g["why"], g["entries"]
                crtc_name = (crtc or {}).get("name")
                if crtc is None:
                    self.add("drm.active", FAIL, True, why)
                else:
                    mode = crtc.get("mode") or {}
                    problems = []
                    if mode.get("clock") != p.clock_khz:
                        problems.append("clock %s kHz, expected %d" % (mode.get("clock"), p.clock_khz))
                    if mode.get("h") != p.h or mode.get("v") != p.v:
                        problems.append("timings %s %s, expected %s %s" % (mode.get("h"), mode.get("v"), p.h, p.v))
                    if mode.get("flags") != p.expected_drm_flags:
                        problems.append("flags 0x%x, expected 0x%x" % (mode.get("flags") or 0, p.expected_drm_flags))
                    self.add("drm.active", FAIL if problems else PASS, True,
                             "%s on active %s: %d kHz %s %s flags 0x%x" % (
                                 p.drm_connector, crtc_name, p.clock_khz, " ".join(map(str, p.h)),
                                 " ".join(map(str, p.v)), p.expected_drm_flags) if not problems
                             else "%s CRTC mode differs from the profile" % p.drm_connector,
                             problems + ["state views: %s" % ", ".join(entries)])
        cdir = getattr(self, "_connector_dir", None)
        if cdir:
            try:
                with open(os.path.join(cdir, "modes")) as fh:
                    modes = [l.strip() for l in fh if l.strip()]
            except OSError:
                modes = []
            if not modes:
                self.add("drm.modes", NOT_TESTED, True, "%s lists no modes (converter off?)" % os.path.basename(cdir))
            elif self._boot_state() == "pending":
                self.add("drm.modes", PENDING, True, "the restricted EDID applies after a reboot", modes)
            else:
                bad = sorted(set(m for m in modes if m != "720x480i"))
                self.add("drm.modes", FAIL if bad else PASS, True,
                         "kernel exposes only 720x480i on %s" % p.drm_connector if not bad
                         else "kernel exposes modes other than 720x480i: %s" % ", ".join(bad), modes)

    def _persistence(self):
        runtime = [c for c in self.checks if c.id in ("x11.mode", "x11.margins", "drm.active", "edid.active")]
        last_install = None
        if self.manifest and not self.runtime_only:
            for run in self.manifest.get("runs", []):
                if run.get("action") == "install" and run.get("finished"):
                    last_install = run["finished"]
        btime = None
        for line in self.root.read_text("/proc/stat", default="").splitlines():
            if line.startswith("btime "):
                btime = int(line.split()[1])
        if last_install and btime is not None:
            import calendar
            import time
            installed_at = calendar.timegm(time.strptime(last_install, "%Y-%m-%dT%H:%M:%SZ"))
            if btime <= installed_at:
                self.add("reboot.persistence", PENDING, True, "the system has not been rebooted since the last install")
                return
        if runtime and all(c.status == PASS for c in runtime):
            self.add("reboot.persistence", PASS, True,
                     "mode, margins and EDID were restored automatically at boot" if last_install
                     else "mode, margins and EDID are active after boot")
        elif any(c.status == FAIL for c in runtime):
            self.add("reboot.persistence", FAIL, True, "runtime state after boot does not match the profile")
        else:
            self.add("reboot.persistence", NOT_TESTED, True, "runtime state could not be fully checked")

    def _manual(self):
        manual = [
            ("manual.power-cycle", "Full power cycle of Pi and converter (unplug both ~30 s), then rerun verify; picture stable"),
            ("manual.visual", "Owner visual acceptance: left/right content and clock/menu visible, no substantial "
                              "curvature, normal colours, no pink/green bands, acceptable centring, test circle round "
                              "(tests/geometry.py)"),
            ("manual.playback", "Fullscreen 4:3 moving video stable with HDMI audio inactive (tests/playback.sh)"),
            ("manual.hdmi-audio", "Whether disabling HDMI audio prevents the corruption has not been physically "
                                  "verified; never test it automatically"),
            ("manual.usb-audio", "Separate USB audio to the TV's analogue inputs is future work"),
        ]
        for cid, text in manual:
            self.add(cid, NOT_TESTED, False, text)


def _edid_decode_failures(text):
    out, grab = [], False
    for line in text.splitlines():
        if line.startswith("Failures:"):
            grab = True
            continue
        if grab:
            if line.startswith("EDID conformity"):
                break
            if line.startswith("  ") and not line.strip().endswith(":"):
                out.append(line.strip())
    return out


def summarize(checks):
    counts = {}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    req = [c for c in checks if c.required]
    if any(c.status == FAIL for c in checks if c.required):
        result, code = "fail", 1
    elif any(c.status in (PENDING, NOT_TESTED) for c in req):
        result, code = "incomplete", 2
    else:
        result, code = "pass", 0
    return counts, result, code


def render_text(checks, profile_id):
    lines = ["rpi-crt-profiles verify - profile %s" % profile_id, ""]
    for c in checks:
        lines.append("%-10s %-22s %s%s" % (c.status, c.id, c.summary, "" if c.required else "  [optional]"))
        for d in c.detail:
            if d:
                lines.append("%-10s %-22s   %s" % ("", "", d))
    counts, result, code = summarize(checks)
    lines.append("")
    lines.append("Summary: " + ", ".join("%d %s" % (counts.get(s, 0), s) for s in (PASS, FAIL, WARN, PENDING, NOT_TESTED)))
    explain = {
        "pass": "all required checks passed",
        "fail": "at least one required check FAILED (see docs/recovery.md)",
        "incomplete": "nothing failed, but required checks are PENDING (reboot) or NOT_TESTED",
    }
    lines.append("Result: %s - %s" % (result.upper(), explain[result]))
    return "\n".join(lines), code


def render_json(checks, profile_id):
    counts, result, code = summarize(checks)
    return json.dumps({"profile": profile_id, "result": result, "exit_code": code, "counts": counts,
                       "checks": [c.as_dict() for c in checks]}, indent=2), code
