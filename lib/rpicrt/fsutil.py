"""Filesystem helpers: staged roots, hashing and atomic replacement."""

import hashlib
import os
import stat
import tempfile


class Root:
    """Maps absolute system paths onto a (possibly staged) filesystem root.

    ``Root("/")`` is the live system. Any other root is a staging tree used by
    tests and is never allowed to run real system commands (see runner.py).
    """

    def __init__(self, root="/"):
        self.root = os.path.abspath(root)

    @property
    def is_live(self):
        return self.root == "/"

    def p(self, path):
        if not path.startswith("/"):
            raise ValueError("system path must be absolute: %r" % path)
        if self.is_live:
            return path
        return os.path.join(self.root, path.lstrip("/"))

    def exists(self, path):
        return os.path.lexists(self.p(path))

    def read_bytes(self, path):
        with open(self.p(path), "rb") as fh:
            return fh.read()

    def read_text(self, path, default=None):
        try:
            with open(self.p(path), "r", encoding="utf-8", errors="surrogateescape") as fh:
                return fh.read()
        except FileNotFoundError:
            if default is not None:
                return default
            raise


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(real_path):
    h = hashlib.sha256()
    with open(real_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def file_info(real_path):
    """Return ownership/mode/hash of an existing regular file, or None."""
    try:
        st = os.lstat(real_path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return {"type": "other", "mode": "%04o" % stat.S_IMODE(st.st_mode), "uid": st.st_uid, "gid": st.st_gid}
    return {
        "type": "file",
        "sha256": sha256_file(real_path),
        "mode": "%04o" % stat.S_IMODE(st.st_mode),
        "uid": st.st_uid,
        "gid": st.st_gid,
        "size": st.st_size,
    }


def _fsync_dir(dirpath):
    try:
        fd = os.open(dirpath, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some filesystems (e.g. vfat) do not support directory fsync
    finally:
        os.close(fd)


def atomic_write(real_path, data, mode=None, owner=None, like=None):
    """Atomically replace ``real_path`` with ``data``.

    The temporary file is created in the destination directory so that the
    final ``rename`` stays on one filesystem. ``like`` copies mode (and owner,
    if running as root) from an existing file; ``mode``/``owner`` override.
    Mode/ownership changes that the filesystem refuses (vfat) are ignored.
    """
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogateescape")
    directory = os.path.dirname(real_path) or "."
    if like is not None and os.path.exists(like):
        st = os.stat(like)
        if mode is None:
            mode = stat.S_IMODE(st.st_mode)
        if owner is None and os.geteuid() == 0:
            owner = (st.st_uid, st.st_gid)
    fd, tmp = tempfile.mkstemp(prefix=".rpi-crt.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        if owner is not None:
            try:
                os.chown(tmp, owner[0], owner[1])
            except OSError:
                pass
        os.replace(tmp, real_path)
        tmp = None
        _fsync_dir(directory)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def makedirs(real_path, mode=0o755):
    """Create missing directories; return the list of directories created."""
    created = []
    path = os.path.abspath(real_path)
    missing = []
    while not os.path.exists(path):
        missing.append(path)
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    for d in reversed(missing):
        os.mkdir(d, mode)
        try:
            os.chmod(d, mode)
        except OSError:
            pass
        created.append(d)
    return created


def is_safe_root_executable(real_path, root):
    """Check that a file is safe for root to execute.

    Returns ``(ok, reason)``. On a staged (test) root ownership checks are
    skipped because the test runner is not root, and the directory walk stops
    at the staging root.
    """
    live = root.is_live
    top = "/" if live else os.path.realpath(root.root)
    try:
        st = os.stat(real_path)
    except FileNotFoundError:
        return False, "does not exist"
    if not stat.S_ISREG(st.st_mode):
        return False, "is not a regular file"
    if not st.st_mode & stat.S_IXUSR:
        return False, "is not executable"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False, "is writable by group or others"
    if live and st.st_uid != 0:
        return False, "is not owned by root"
    real = os.path.realpath(real_path)
    sys_view = real if live else "/" + os.path.relpath(real, top)
    for prefix in ("/home/", "/root/", "/tmp/", "/var/tmp/", "/run/user/"):
        if sys_view.startswith(prefix):
            return False, "lives under %s (user-writable location)" % prefix.rstrip("/")
    # Walk up the directory chain; every directory must be root-owned and
    # not group/other writable (sticky /tmp-style directories are rejected too).
    d = os.path.dirname(os.path.realpath(real_path))
    while True:
        dst = os.stat(d)
        if dst.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return False, "parent directory %s is writable by group or others" % d
        if live and dst.st_uid != 0:
            return False, "parent directory %s is not owned by root" % d
        parent = os.path.dirname(d)
        if parent == d or d == top:
            break
        d = parent
    return True, "ok"
