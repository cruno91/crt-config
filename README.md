# rpi-crt-profiles

Reproducible CRT display profiles for Raspberry Pi. The first profile,
`sony-trinitron-lontium`, drives a Sony Trinitron at NTSC 720×480i through an active
Lontium HDMI-to-YPbPr (component) converter, on a clean Raspberry Pi OS Desktop
installation.

It is a hardware-enablement layer only.

```sh
git clone <repository-url> rpi-crt-profiles
cd rpi-crt-profiles
sudo ./install.sh --profile sony-trinitron-lontium --dry-run   # show the plan and diffs
sudo ./install.sh --profile sony-trinitron-lontium
sudo reboot
sudo ./verify.sh --profile sony-trinitron-lontium
```

> **Status:** the profile values come from one working Raspberry Pi 5. The tools are
> tested offline (CI) but have not yet run on a Pi.

## What the profile sets up

| Layer | Setting |
|---|---|
| EDID | `/lib/firmware/edid/converter-crt.bin`: the converter's EDID restricted to CTA VIC 6 (480i 4:3) ([docs/edid.md](docs/edid.md)) |
| Kernel | `drm.edid_firmware=HDMI-A-1:edid/converter-crt.bin video=HDMI-A-1:720x480@60iD` appended to `cmdline.txt` |
| Initramfs | hook `/etc/initramfs-tools/hooks/rpi-crt-edid` puts the EDID into the initramfs |
| X11 | LightDM `display-setup-script` → `/usr/local/libexec/rpi-crt/display-setup`, which registers `720x480i-crt` with `crt-xrandr` (13.5 MHz, H `720 779 841 858`, V `480 488 494 525`, −H −V interlace + pixel repetition, DRM flags `0x101a`) and applies margins L8 R48 T16 B16 |
| Audio | policy `disabled` (default) adds `noaudio` to `dtoverlay=vc4-kms-v3d`; `preserve` leaves it alone ([docs/known-issues.md](docs/known-issues.md)) |

State, the install manifest and the original copies of changed files are kept in
`/var/lib/rpi-crt/` (root, 0700). The original `cmdline.txt` and `config.txt` are also
copied to `*.rpi-crt-baseline` on the boot partition for recovery from another computer.

## Requirements

- Raspberry Pi OS **Desktop** (Debian 13 trixie, arm64) with **X11** and LightDM.
  Wayland sessions are refused; switch with `sudo raspi-config` → Advanced Options →
  Wayland → X11.
- `python3`. Missing build packages (`gcc libc6-dev pkg-config libx11-dev
  libxrandr-dev x11-xserver-utils initramfs-tools`) are installed with
  `apt-get install --no-install-recommends`, never by upgrading.
  `--with-diagnostics` also installs `edid-decode libdrm-dev libdrm-tests mpv ffmpeg python3-tk`.

## install.sh

```text
sudo ./install.sh --profile NAME [--dry-run] [--hdmi-audio disabled|preserve]
                  [--migrate-legacy] [--replace-display-hook]
                  [--allow-untested-hardware] [--allow-untested-os]
                  [--no-install-deps] [--with-diagnostics]
```

- **Preflight** checks everything first (board, OS, boot files, overlay, LightDM, DRM
  connector, EDID assets, packages) and changes nothing if any check fails.
- **Order**: packages → build the helper → install files → rebuild the initramfs and
  confirm the EDID is in it → `config.txt` → `lightdm.conf` (checked with
  `lightdm --show-config`) → `cmdline.txt` last, so the EDID override never points at a
  missing file.
- **Idempotent**: rerunning changes nothing. Every step is journaled first, so an
  interrupted run can be resumed by rerunning or rolled back with `uninstall.sh`. The
  first-seen original of each file is never overwritten.
- It never restarts LightDM, never reboots and never touches SSH. The checkout runs as
  root under `sudo`, so keep it somewhere only you can write.

Existing configuration:

| Situation | Behaviour |
|---|---|
| Existing safe `display-setup-script` (root-owned, not group/other-writable, not under `/home` or `/tmp`), such as Raspberry Pi OS's `/usr/share/dispsetup.sh` | chained: runs first, then the CRT mode is applied |
| Unsafe existing hook | refused; `--replace-display-hook` replaces it |
| `[Seat:seat0]`-style override of `display-setup-script` | refused |
| Conflicting or duplicate `drm.edid_firmware`/`video=` arguments | refused |
| Several or conditional `vc4-kms-v3d` lines | refused |
| Hand-made setup (`/etc/initramfs-tools/hooks/crt-edid`, `/usr/local/bin/crt-display-setup.sh`) | refused; `--migrate-legacy` takes it over reversibly |
| Untested board or OS | refused; `--allow-untested-hardware` / `--allow-untested-os` |

## verify.sh

```text
sudo ./verify.sh [--profile NAME] [--runtime-only] [--json] [--display :0] [--xauthority FILE]
```

Read-only: it never plays audio or changes modes. It checks the installed files and the
live state (EDID in use, boot initramfs, running kernel arguments, X mode and margins,
DRM CRTC timings and flags, audio devices). Statuses: `PASS`, `FAIL`, `WARN`, `PENDING`
(needs a reboot), `NOT_TESTED` (including physical checks only a person can do).
Exit code `0` = all required checks pass, `1` = a failure, `2` = pending or untested.
`--runtime-only` checks a system that this tool did not install.

## uninstall.sh

```text
sudo ./uninstall.sh [--dry-run]
```

Restores owned settings and files only if they still hold the installed values.
Anything changed since installing is reported and left alone. It never removes an EDID
that `cmdline.txt` still references, or a script LightDM still runs. Packages are kept.

## Recovery

See [docs/recovery.md](docs/recovery.md). In short: `sudo ./uninstall.sh && sudo
reboot` over SSH. If the Pi is unreachable, copy the `*.rpi-crt-baseline` files over the
originals on the SD card's boot partition. For HDMI corruption, fully power-cycle the Pi
**and** the converter.

## Diagnostics on the CRT (run as the desktop user)

```sh
DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" tests/geometry.py --seconds 60   # grid, safe areas, round circle
DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" tests/playback.sh --seconds 300  # 4:3 test clip, no audio
```

## Repository layout

```text
install.sh verify.sh uninstall.sh   entry points (wrappers around lib/rpicrt)
profiles/                           profiles (parsed and validated, never sourced)
lib/rpicrt/                         implementation (Python standard library)
src/crt-xrandr.c                    XRandR mode helper, compiled on install
src/crt-shift.c                     optional sync-shift calibration tool (not installed)
edid/                               original and restricted EDID, generator
templates/                          display-setup and initramfs hook templates
reference/                          files from the original hand-made setup
tests/                              offline suite, mocks, fixtures, container, on-device tools
docs/                               EDID, compatibility, known issues, recovery
```

## Tests

```sh
tests/test-installer.sh     # anywhere with python3; C-helper tests skip without X11 headers
tests/run-container.sh      # full suite in Debian trixie arm64 (needs Docker)
```

Installer runs target throw-away staged roots with mocked system commands; a staged root
refuses to run real ones. Nothing exercises real VC4, HDMI, LightDM or the CRT.
