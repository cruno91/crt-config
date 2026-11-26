"""Install manifest, baseline backups and locking.

Layout (root-owned, mode 0700)::

    /var/lib/rpi-crt/
      manifest.json         what is installed and the original (baseline) state
      baseline/<path>       first-seen copy of every file we changed; never overwritten
      runs/<stamp>/<path>   copy taken immediately before each modification
      legacy/<path>         hand-made files moved aside with --migrate-legacy
      history/<stamp>/      manifest + backups of completed uninstalls
      lock

Every change follows write-ahead journaling: the manifest entry (with the
prior state) is saved before the file is modified, and marked ``applied``
afterwards. An interrupted run therefore never loses the original baseline,
and a rerun of install or uninstall can complete or reverse it.
"""

import contextlib
import datetime
import fcntl
import json
import os
import shutil

from . import fsutil
from .platform import STATE_DIR

SCHEMA = 1


class StateError(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


class State:
    def __init__(self, root):
        self.root = root
        self.sys_dir = STATE_DIR
        self.dir = root.p(STATE_DIR)
        self.manifest_path = os.path.join(self.dir, "manifest.json")
        self.run_stamp = stamp()

    def load(self):
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None
        except ValueError as exc:
            raise StateError("manifest %s is corrupt: %s" % (self.manifest_path, exc))
        if data.get("schema") != SCHEMA:
            raise StateError("unsupported manifest schema %r" % data.get("schema"))
        return data

    def save(self, manifest):
        self.ensure_dir()
        manifest["updated"] = now()
        data = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        fsutil.atomic_write(self.manifest_path, data, mode=0o600)

    def ensure_dir(self):
        if not os.path.isdir(self.dir):
            fsutil.makedirs(self.dir, mode=0o700)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass

    @contextlib.contextmanager
    def lock(self):
        self.ensure_dir()
        fd = os.open(os.path.join(self.dir, "lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StateError("another rpi-crt-profiles run holds %s/lock" % self.sys_dir)
            yield
        finally:
            os.close(fd)

    def _store(self, area, sys_path):
        rel = sys_path.lstrip("/")
        dest = os.path.join(self.dir, area, rel)
        fsutil.makedirs(os.path.dirname(dest), mode=0o700)
        return dest, "%s/%s" % (area, rel)

    def capture_prior(self, sys_path):
        """Describe the current state of ``sys_path`` and keep a baseline copy.

        The baseline copy is only written if none exists yet, so reinstalling
        never replaces the original recovery copy.
        """
        real = self.root.p(sys_path)
        info = fsutil.file_info(real)
        if info is None:
            return {"absent": True}
        if info["type"] != "file":
            raise StateError("%s exists but is not a regular file" % sys_path)
        dest, rel = self._store("baseline", sys_path)
        if not os.path.exists(dest):
            shutil.copy2(real, dest)
            os.chmod(dest, 0o600)
        elif fsutil.sha256_file(dest) != info["sha256"]:
            # Baseline already holds an older original; keep it and record
            # this intermediate copy in the run area instead.
            dest, rel = self._store("runs/" + self.run_stamp + "-prior", sys_path)
            shutil.copy2(real, dest)
            os.chmod(dest, 0o600)
        prior = {"absent": False, "backup": rel}
        prior.update({k: info[k] for k in ("sha256", "mode", "uid", "gid")})
        return prior

    def run_backup(self, sys_path):
        """Copy the file as it is right now into this run's backup area."""
        real = self.root.p(sys_path)
        if not os.path.isfile(real):
            return None
        dest, rel = self._store("runs/" + self.run_stamp, sys_path)
        if not os.path.exists(dest):
            shutil.copy2(real, dest)
            os.chmod(dest, 0o600)
        return rel

    def backup_path(self, rel):
        return os.path.join(self.dir, rel)

    def move_aside(self, sys_path):
        real = self.root.p(sys_path)
        dest, rel = self._store("legacy", sys_path)
        if os.path.exists(dest):
            raise StateError("legacy backup %s already exists" % dest)
        shutil.copy2(real, dest)
        os.unlink(real)
        return rel

    def archive(self):
        """Move manifest and backups to history/<stamp>/ after a clean uninstall."""
        hist = os.path.join(self.dir, "history", self.run_stamp)
        fsutil.makedirs(hist, mode=0o700)
        for name in ("manifest.json", "baseline", "runs", "legacy"):
            src = os.path.join(self.dir, name)
            if os.path.exists(src):
                shutil.move(src, os.path.join(hist, name))
        return "%s/history/%s" % (self.sys_dir, self.run_stamp)


def new_manifest(profile, options, tool_version):
    return {
        "schema": SCHEMA,
        "tool_version": tool_version,
        "state": "installing",
        "created": now(),
        "updated": now(),
        "profile_id": profile.id,
        "profile": dict(profile.raw),
        "options": dict(options),
        "runs": [],
        "dirs_created": [],
        "files": {},
        "edits": {},
        "legacy": {},
    }
