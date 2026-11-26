"""Platform facts and well-known system paths."""

import glob
import os
import re

BOOT_DIR = "/boot/firmware"
CMDLINE = BOOT_DIR + "/cmdline.txt"
CONFIG_TXT = BOOT_DIR + "/config.txt"
OVERLAY_README = BOOT_DIR + "/overlays/README"
CMDLINE_BASELINE_COPY = BOOT_DIR + "/cmdline.txt.rpi-crt-baseline"
CONFIG_BASELINE_COPY = BOOT_DIR + "/config.txt.rpi-crt-baseline"

EDID_DIR = "/lib/firmware/edid"
LIBEXEC_DIR = "/usr/local/libexec/rpi-crt"
HELPER = LIBEXEC_DIR + "/crt-xrandr"
DISPLAY_SETUP = LIBEXEC_DIR + "/display-setup"
INITRAMFS_HOOK = "/etc/initramfs-tools/hooks/rpi-crt-edid"
STATE_DIR = "/var/lib/rpi-crt"
LOG_FILE = "/var/log/rpi-crt/display-setup.log"

# Files of a hand-made setup that --migrate-legacy takes over.
LEGACY_INITRAMFS_HOOK = "/etc/initramfs-tools/hooks/crt-edid"
LEGACY_DISPLAY_SETUP = "/usr/local/bin/crt-display-setup.sh"
LEGACY_HELPER = "/usr/local/bin/crt-xrandr"

DEVICE_TREE_MODEL = "/proc/device-tree/model"
OS_RELEASE = "/etc/os-release"
KERNEL_RELEASE = "/proc/sys/kernel/osrelease"
PROC_CMDLINE = "/proc/cmdline"
ASOUND_CARDS = "/proc/asound/cards"
SYS_DRM = "/sys/class/drm"
DEBUGFS_DRI = "/sys/kernel/debug/dri"
DEFAULT_DM = "/etc/X11/default-display-manager"
LIGHTDM_BIN = "/usr/sbin/lightdm"
LIGHTDM_XAUTH = "/var/run/lightdm/root/:0"

REQUIRED_PACKAGES = (
    "gcc", "libc6-dev", "pkg-config", "libx11-dev", "libxrandr-dev",
    "x11-xserver-utils", "initramfs-tools",
)
DIAGNOSTIC_PACKAGES = (
    "edid-decode", "libdrm-dev", "libdrm-tests", "mpv", "ffmpeg", "python3-tk",
)


def read_model(root):
    try:
        raw = root.read_bytes(DEVICE_TREE_MODEL)
    except FileNotFoundError:
        return None
    return raw.split(b"\x00")[0].decode("ascii", "replace").strip()


def model_matches(model, tested):
    return any(model == t or model.startswith(t + " ") for t in tested)


def read_os_release(root):
    out = {}
    text = root.read_text(OS_RELEASE, default="")
    for line in text.splitlines():
        m = re.match(r'^([A-Z_]+)=("?)(.*)\2$', line.strip())
        if m:
            out[m.group(1)] = m.group(3)
    return out


def kernel_release(root):
    return root.read_text(KERNEL_RELEASE, default="").strip() or None


def boot_initramfs_name(release):
    """Name of the firmware-loaded initramfs for a Raspberry Pi kernel release."""
    if not release:
        return None
    if release.endswith("-2712"):
        return "initramfs_2712"
    if release.endswith("-v8"):
        return "initramfs8"
    if release.endswith("-v7l"):
        return "initramfs7l"
    if release.endswith("-v7"):
        return "initramfs7"
    return None


def connector_dirs(root, connector):
    """Real paths of /sys/class/drm/card*-<connector>, de-duplicated."""
    pattern = root.p(SYS_DRM) + "/card*-" + connector
    seen = {}
    for path in sorted(glob.glob(pattern)):
        if not re.search(r"/card[0-9]+-" + re.escape(connector) + "$", path):
            continue
        seen.setdefault(os.path.realpath(path), path)
    return sorted(seen.values())


def asound_cards(root):
    """Parse /proc/asound/cards into [(index, id, driver, description)]."""
    cards = []
    text = root.read_text(ASOUND_CARDS, default="")
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\s+\[([^\]]+?)\s*\]:\s*(\S+)\s+-\s+(.*)$", line)
        if m:
            cards.append((int(m.group(1)), m.group(2).strip(), m.group(3), m.group(4).strip()))
    return cards


def hdmi_audio_cards(cards):
    return [c for c in cards if c[1].lower().startswith("vc4hdmi") or c[2].lower().startswith("vc4-hdmi")]
