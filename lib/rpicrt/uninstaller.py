"""uninstall: restore the original owned settings, remove installed files.

Rules:
* An owned setting is restored only if it still holds the value we wrote;
  anything changed since installation is reported as a conflict and left
  alone (with manual recovery steps).
* Files are removed or restored from the baseline only if they still have
  the content we installed.
* Files that the boot or display configuration still references (because a
  conflicting edit could not be reverted) are kept, so an uninstall can
  never leave the kernel pointing at a missing EDID or LightDM at a missing
  display-setup script.
* Shared packages are never removed.
"""

import os
import shutil

from . import cmdline as cmdline_mod
from . import configtxt, fsutil, lightdm, platform as plat
from .installer import contains_edid
from .runner import Runner
from .state import State, StateError, now

EDIT_MODULES = {"cmdline": cmdline_mod, "lightdm": lightdm, "config_txt": configtxt}
UNINSTALL_ORDER = ("cmdline", "lightdm", "config_txt")  # disable the EDID override first


class UninstallError(Exception):
    pass


class Uninstaller:
    def __init__(self, root, dry_run=False, out=None):
        self.root = root
        self.dry_run = dry_run
        self.out = out or (lambda s="": print(s))
        self.runner = Runner(root)
        self.state = State(root)
        self.conflicts = []
        self.actions = []
        self.planned = {}  # path -> text after this run (lets --dry-run reason correctly)

    def run(self):
        if self.root.is_live and not self.dry_run and os.geteuid() != 0:
            raise UninstallError("must run as root (use sudo), or use --dry-run")
        m = self.state.load()
        if m is None:
            raise UninstallError("nothing to uninstall: no manifest at %s/manifest.json" % plat.STATE_DIR)
        if self.dry_run:
            self._process(m)
            return self.conflicts
        with self.state.lock():
            m["state"] = "uninstalling"
            m["runs"].append({"action": "uninstall", "started": now(), "stamp": self.state.run_stamp})
            self.state.save(m)
            self._process(m)
            if self.conflicts:
                m["state"] = "uninstall-incomplete"
                m["runs"][-1].update({"finished": now(), "result": "conflicts", "conflicts": list(self.conflicts)})
                self.state.save(m)
            else:
                m["state"] = "uninstalled"
                m["runs"][-1].update({"finished": now(), "result": "ok"})
                self.state.save(m)
                where = self.state.archive()
                self.out("Install state archived to %s" % where)
        return self.conflicts

    def _write(self, sys_path, text):
        if self.dry_run:
            return
        self.state.run_backup(sys_path)
        fsutil.atomic_write(self.root.p(sys_path), text, like=self.root.p(sys_path))

    def _process(self, m):
        initramfs_dirty = False
        model = plat.read_model(self.root) or ""
        for kind in UNINSTALL_ORDER:
            entry = m["edits"].get(kind)
            if not entry:
                continue
            path = entry["path"]
            if not self.root.exists(path):
                self.conflicts.append("%s is missing; cannot restore its original settings" % path)
                continue
            text = self.root.read_text(path)
            try:
                new_text, conflicts = EDIT_MODULES[kind].plan_uninstall(text, entry["record"])
            except Exception as exc:  # parse errors from a now-malformed file
                conflicts, new_text = ["%s: %s" % (path, exc)], text
            if conflicts:
                self.conflicts.extend(conflicts)
                entry["status"] = "conflict"
            else:
                self.planned[path] = new_text
                if new_text != text:
                    self.actions.append("restore owned settings in %s" % path)
                    self.out("  restoring original settings in %s" % path)
                    self._write(path, new_text)
                else:
                    self.out("  %s already has its original settings" % path)
                if not self.dry_run:
                    del m["edits"][kind]
            if not self.dry_run:
                self.state.save(m)

        keep = self._still_referenced(m, model)

        for path, entry in sorted(m.get("legacy", {}).items()):
            if self.root.exists(path):
                self.conflicts.append("cannot restore %s from %s/%s: the path is occupied"
                                      % (path, plat.STATE_DIR, entry["moved_to"]))
                continue
            self.out("  restoring hand-made %s" % path)
            initramfs_dirty = True
            if not self.dry_run:
                real = self.root.p(path)
                fsutil.makedirs(os.path.dirname(real))
                shutil.copy2(self.state.backup_path(entry["moved_to"]), real)
                if self.root.is_live:
                    os.chown(real, 0, 0)
                del m["legacy"][path]
                self.state.save(m)

        for path in sorted(list(m.get("files", {})), reverse=True):
            entry = m["files"][path]
            if path in keep:
                self.conflicts.append("kept %s because %s" % (path, keep[path]))
                continue
            changed = self._restore_file(path, entry)
            if changed and (path == plat.INITRAMFS_HOOK or path.startswith(plat.EDID_DIR + "/")):
                initramfs_dirty = True
            if changed is not None and not self.dry_run:
                del m["files"][path]
                self.state.save(m)

        for d in sorted(m.get("dirs_created", []), key=len, reverse=True):
            real = self.root.p(d)
            if os.path.isdir(real) and not os.listdir(real):
                self.out("  removing empty directory %s" % d)
                if not self.dry_run:
                    os.rmdir(real)
            if not self.dry_run and not os.path.exists(real):
                m["dirs_created"].remove(d)

        if initramfs_dirty:
            self._rebuild_initramfs(m, model)

    def _still_referenced(self, m, model):
        """Files that must stay because remaining configuration uses them."""
        keep = {}
        profile = m.get("profile", {})
        edid_file = profile.get("EDID_FILE")
        try:
            tokens, _ = cmdline_mod.split(self.planned.get(plat.CMDLINE) or self.root.read_text(plat.CMDLINE))
        except Exception:
            tokens = []
        referenced = [t for t in tokens if t.startswith(cmdline_mod.EDID_KEY + "=") and ("edid/%s" % edid_file) in t]
        if edid_file and referenced:
            edid_path = plat.EDID_DIR + "/" + edid_file
            if edid_path in m.get("files", {}) and m["files"][edid_path]["prior"].get("absent", True):
                keep[edid_path] = "cmdline.txt still references edid/%s (%s)" % (edid_file, referenced[0])
                legacy_hook_returns = plat.LEGACY_INITRAMFS_HOOK in m.get("legacy", {})
                if plat.INITRAMFS_HOOK in m.get("files", {}) and not legacy_hook_returns:
                    keep[plat.INITRAMFS_HOOK] = "the kept EDID must stay in the initramfs"
        files = [(path, self.planned.get(path, text)) for path, text in lightdm.config_files(self.root)]
        value = (lightdm.effective_seat(files).get(lightdm.KEY) or (None,))[0]
        if value == plat.DISPLAY_SETUP:
            for path in (plat.DISPLAY_SETUP, plat.HELPER):
                if path in m.get("files", {}):
                    keep[path] = "LightDM still runs %s" % plat.DISPLAY_SETUP
        return keep

    def _restore_file(self, path, entry):
        """Return True if changed, False if already original, None on conflict."""
        real = self.root.p(path)
        cur = fsutil.file_info(real)
        prior = entry.get("prior", {"absent": True})
        if cur is not None and cur.get("type") != "file":
            self.conflicts.append("%s is no longer a regular file; left unchanged" % path)
            return None
        cur_sha = cur.get("sha256") if cur else None
        if cur_sha is not None and cur_sha != entry.get("sha256") and cur_sha != prior.get("sha256"):
            self.conflicts.append("%s was modified after installation; left unchanged (original: %s)"
                                  % (path, "none" if prior.get("absent") else "%s/%s" % (plat.STATE_DIR, prior["backup"])))
            return None
        if prior.get("absent"):
            if cur is None:
                return False
            self.out("  removing %s" % path)
            if not self.dry_run:
                self.state.run_backup(path)
                os.unlink(real)
            return True
        if cur_sha == prior.get("sha256"):
            self.out("  %s already has its original content" % path)
            return False
        self.out("  restoring original %s" % path)
        if not self.dry_run:
            backup = self.state.backup_path(prior["backup"])
            if not os.path.isfile(backup) or fsutil.sha256_file(backup) != prior["sha256"]:
                self.conflicts.append("baseline backup for %s is missing or damaged; left unchanged" % path)
                return None
            with open(backup, "rb") as fh:
                data = fh.read()
            if cur is not None:
                self.state.run_backup(path)
            owner = (prior["uid"], prior["gid"]) if self.root.is_live else None
            fsutil.makedirs(os.path.dirname(real))
            fsutil.atomic_write(real, data, mode=int(prior["mode"], 8), owner=owner)
        return True

    def _rebuild_initramfs(self, m, model):
        self.out("  rebuilding initramfs (update-initramfs -u -k all)")
        if self.dry_run:
            return
        res = self.runner.run(["update-initramfs", "-u", "-k", "all"], timeout=1800)
        if not res.ok:
            self.conflicts.append("update-initramfs failed; rerun 'sudo update-initramfs -u -k all' manually:\n%s"
                                  % (res.stderr or res.stdout)[-1500:])
            return
        # If the boot configuration still references the EDID, it must still be inside.
        edid_file = m.get("profile", {}).get("EDID_FILE")
        try:
            tokens, _ = cmdline_mod.split(self.root.read_text(plat.CMDLINE))
        except Exception:
            tokens = []
        if edid_file and any(t.startswith(cmdline_mod.EDID_KEY + "=") and edid_file in t for t in tokens):
            boot = (m.get("initramfs") or {}).get("boot_file") or (
                plat.BOOT_DIR + "/" + (plat.boot_initramfs_name(plat.kernel_release(self.root) or "") or "initramfs8"))
            if not contains_edid(self.runner, self.root, boot, edid_file):
                self.conflicts.append("cmdline.txt still references edid/%s but %s does not contain it; check the "
                                      "restored initramfs hooks before rebooting" % (edid_file, boot))
