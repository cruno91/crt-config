"""Shared helpers for the offline test-suite: staged root filesystems and CLI runs.

Nothing here touches the real system. Staged roots live in temporary
directories and every external command resolves to tests/mocks/bin.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.path.join(REPO, "lib")
FIXTURES = os.path.join(REPO, "tests", "fixtures")
MOCK_BIN = os.path.join(REPO, "tests", "mocks", "bin")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

# Staged roots must look like a real system (directories 0755): with a
# group-writable umask the installer would rightly refuse to trust them.
os.umask(0o022)

MODEL = "Raspberry Pi 5 Model B Rev 1.0"            # the tested board
UNTESTED_MODEL = "Raspberry Pi 3 Model B Plus Rev 1.3"  # any board outside the profile


def slurp(path):
    with open(path) as fh:
        return fh.read()


def fixture(name, mode="r"):
    with open(os.path.join(FIXTURES, name), mode) as fh:
        return fh.read()


def write(root, path, data, mode=0o644):
    real = os.path.join(root, path.lstrip("/"))
    os.makedirs(os.path.dirname(real), exist_ok=True)
    with open(real, "wb" if isinstance(data, bytes) else "w") as fh:
        fh.write(data)
    os.chmod(real, mode)
    return real


def read(root, path, mode="r"):
    with open(os.path.join(root, path.lstrip("/")), mode) as fh:
        return fh.read()


def exists(root, path):
    return os.path.lexists(os.path.join(root, path.lstrip("/")))


def make_root(variant="stock", model=MODEL, kernel="6.18.50+rpt-rpi-v8", codename="trixie",
              session="rpd-x", connector_status="connected"):
    """Build a staged Raspberry Pi OS root.

    variant 'stock':  fresh Raspberry Pi OS Desktop (stock dispsetup.sh hook active)
    variant 'legacy': the archived hand-made CRT setup
    """
    root = tempfile.mkdtemp(prefix="rpi-crt-root.")
    write(root, "/proc/device-tree/model", model.encode() + b"\x00")
    write(root, "/etc/os-release",
          'PRETTY_NAME="Debian GNU/Linux 13 (%s)"\nNAME="Debian GNU/Linux"\nVERSION_ID="13"\n'
          'VERSION_CODENAME=%s\nID=debian\n' % (codename, codename))
    write(root, "/proc/sys/kernel/osrelease", kernel + "\n")
    write(root, "/proc/stat", "cpu  1 2 3\nbtime 1000\n")
    write(root, "/proc/asound/cards",
          " 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
          "                      bcm2835 Headphones\n"
          " 1 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n"
          "                      vc4-hdmi-0\n")
    write(root, "/boot/firmware/overlays/README", fixture("overlays-README.excerpt"))
    write(root, "/boot/firmware/initramfs8", "usr/lib/modules/x/vc4.ko.xz\n")
    write(root, "/usr/sbin/lightdm", "", 0o755)
    write(root, "/usr/bin/Xorg", "", 0o755)
    write(root, "/etc/X11/default-display-manager", "/usr/sbin/lightdm\n")
    write(root, "/usr/share/xsessions/rpd-x.desktop", "[Desktop Entry]\nName=X\n")
    write(root, "/usr/share/wayland-sessions/rpd-labwc.desktop", "[Desktop Entry]\nName=W\n")
    write(root, "/usr/share/dispsetup.sh", "#!/bin/sh\nexit 0\n", 0o755)
    os.makedirs(os.path.join(root, "etc/initramfs-tools/hooks"))
    os.makedirs(os.path.join(root, "lib/firmware"))
    os.makedirs(os.path.join(root, "sys/kernel/debug/dri/1"))
    edid = fixture("../../edid/converter-crt.bin", "rb")
    orig = fixture("../../edid/lontium-original.bin", "rb")
    write(root, "/sys/class/drm/card1-HDMI-A-1/status", connector_status + "\n")
    write(root, "/sys/class/drm/card1-HDMI-A-2/status", "disconnected\n")
    write(root, "/sys/class/drm/card1-HDMI-A-2/edid", b"")

    if variant == "stock":
        write(root, "/boot/firmware/cmdline.txt", fixture("cmdline.txt.stock"), 0o755)
        write(root, "/boot/firmware/config.txt", fixture("config.txt.stock"), 0o755)
        text = fixture("lightdm.conf.stock")
        # The original converter EDID before the override is active.
        write(root, "/sys/class/drm/card1-HDMI-A-1/edid", orig)
        write(root, "/sys/class/drm/card1-HDMI-A-1/modes", "1920x1080\n1280x720\n720x480\n")
        write(root, "/proc/cmdline", "coherent_pool=1M " + fixture("cmdline.txt.stock"))
    elif variant == "legacy":
        write(root, "/boot/firmware/cmdline.txt", fixture("cmdline.txt.legacy"), 0o755)
        write(root, "/boot/firmware/config.txt", fixture("config.txt.legacy"), 0o755)
        text = fixture("lightdm.conf.legacy")
        write(root, "/etc/initramfs-tools/hooks/crt-edid", fixture("initramfs-hook-crt-edid.legacy"), 0o755)
        write(root, "/usr/local/bin/crt-display-setup.sh", fixture("crt-display-setup.sh.legacy"), 0o755)
        write(root, "/usr/local/bin/crt-xrandr", b"\x7fELF-legacy-binary", 0o755)
        write(root, "/lib/firmware/edid/converter-crt.bin", edid)
        write(root, "/lib/firmware/edid/converter-480i.bin", b"\x00\xff\xff\xff\xff\xff\xff\x00legacy")
        write(root, "/sys/class/drm/card1-HDMI-A-1/edid", edid)
        write(root, "/sys/class/drm/card1-HDMI-A-1/modes", "720x480i\n720x480i\n")
        write(root, "/proc/cmdline", "coherent_pool=1M " + fixture("cmdline.txt.legacy"))
        write(root, "/proc/asound/cards",
              " 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
              "                      bcm2835 Headphones\n")
        write(root, "/boot/firmware/initramfs8",
              "usr/lib/modules/x/vc4.ko.xz\nusr/lib/firmware/edid/converter-480i.bin\n"
              "usr/lib/firmware/edid/converter-crt.bin\n")
    else:
        raise ValueError(variant)
    if session != "rpd-x":
        text = text.replace("autologin-session=rpd-x", "autologin-session=%s" % session)
        text = text.replace("user-session=rpd-x", "user-session=%s" % session)
    write(root, "/etc/lightdm/lightdm.conf", text)
    return root


def boot_after_install(root, xrandr_fixture="xrandr-verbose.archived.txt"):
    """Simulate a reboot into the installed configuration for runtime checks."""
    write(root, "/proc/cmdline", "coherent_pool=1M " + read(root, "/boot/firmware/cmdline.txt"))
    write(root, "/proc/stat", "cpu  1 2 3\nbtime 4102444800\n")  # year 2100: after any install
    write(root, "/sys/class/drm/card1-HDMI-A-1/edid", read(root, "/lib/firmware/edid/converter-crt.bin", "rb"))
    write(root, "/sys/class/drm/card1-HDMI-A-1/modes", "720x480i\n720x480i\n")
    write(root, "/proc/asound/cards",
          " 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
          "                      bcm2835 Headphones\n")
    write(root, "/sys/kernel/debug/dri/1/state", fixture("drm-state.active.txt"))
    os.symlink("1", os.path.join(root, "sys/kernel/debug/dri/0")) if not exists(root, "/sys/kernel/debug/dri/0") else None
    return os.path.join(FIXTURES, xrandr_fixture)


def snapshot(root, exclude=("var/lib/rpi-crt",)):
    """Map of relative path -> (sha256, mode) for every regular file."""
    out = {}
    for d, dirs, files in os.walk(root):
        rel_d = os.path.relpath(d, root)
        if any(rel_d == e or rel_d.startswith(e + "/") for e in exclude):
            dirs[:] = []
            continue
        for f in files:
            p = os.path.join(d, f)
            if os.path.islink(p):
                continue
            with open(p, "rb") as fh:
                h = hashlib.sha256(fh.read()).hexdigest()
            out[os.path.relpath(p, root)] = (h, oct(os.stat(p).st_mode & 0o7777))
        for dd in list(dirs):
            if os.path.islink(os.path.join(d, dd)):
                dirs.remove(dd)
        out.update({os.path.relpath(os.path.join(d, dd), root) + "/": ("dir", "") for dd in dirs})
    return out


class Run:
    def __init__(self, proc):
        self.rc = proc.returncode
        self.out = proc.stdout
        self.err = proc.stderr

    def __repr__(self):
        return "rc=%s\n--- stdout\n%s\n--- stderr\n%s" % (self.rc, self.out, self.err)


def cli(cmd, root, *args, env=None, log=None):
    e = {"PATH": "/usr/bin:/bin", "RPI_CRT_MOCK_BIN": MOCK_BIN, "HOME": root}
    if log:
        e["RPI_CRT_MOCK_LOG"] = log
    e.update(env or {})
    proc = subprocess.run(
        [sys.executable, "-I", "-B", os.path.join(LIB, "rpicrt", "cli.py"), cmd, "--root", root] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=e, timeout=120)
    return Run(proc)


def cleanup(root):
    shutil.rmtree(root, ignore_errors=True)
