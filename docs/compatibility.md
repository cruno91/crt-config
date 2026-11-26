# Compatibility

`sony-trinitron-lontium` is known to work on exactly one setup. It makes no claims for
other boards, converters (including other Lontium-based ones) or Trinitron models.

## Hardware

```text
Raspberry Pi 5 HDMI 0 ──HDMI──▶ Lontium HDMI→YPbPr converter ──Y/Pb/Pr──▶ Sony Trinitron, rear VIDEO 4
```

| Part | Known | Unknown |
|---|---|---|
| Computer | Raspberry Pi 5 Model B. HDMI 0 is the micro-HDMI port nearest USB-C power: DRM `HDMI-A-1`, XRandR `HDMI-1` | |
| Converter | Active HDMI → component, EDID name `LONTIUM`, does not scale | Chipset, revision, power arrangement; which of ASINs `B0CRNSB69Z`/`B0CQSZCH7D` is the converter and which the cable |
| Television | Sony Trinitron consumer CRT, 4:3, NTSC 480i, component input `VIDEO 4` | Exact model |

Never send progressive 480p/720p/1080p to this CRT through a non-scaling converter.
The restricted EDID exists to prevent it.

## Tested software

| Item | Version |
|---|---|
| OS | Raspberry Pi OS, Debian 13.7 (trixie), arm64 |
| Kernel | `6.18.50+rpt-rpi-v8` (`1:6.18.50-1+rpt1`) |
| Firmware | `raspi-firmware 1:1.20260915-1` (`a86983925695a7e63166327d7c002d64040ed31d`) |
| X / RandR | `xserver-xorg-core 2:21.1.16-1.3+rpt1+deb13u4`, RandR 1.6, `libxrandr2 2:1.5.4-1+b3` |
| LightDM | `lightdm 1.32.0-6+rpt2`, session `rpd-x` (X11) |
| libdrm | `libdrm2 2.4.134-3~bpo13+1+rpt1` |

The installer refuses other boards and OS releases unless `--allow-untested-hardware`
or `--allow-untested-os` is given, and warns if the kernel differs.

## Upgrades

Kernel, firmware, X or LightDM upgrades can change VC4 HDMI behaviour (interlace, pixel
repetition, margins, audio). After any upgrade, reboot and run `sudo ./verify.sh`; all
runtime checks must pass again. The initramfs hook runs on every kernel update, so the
EDID stays in new initramfs images.

## Adding another TV or converter

1. Capture the converter's original EDID and generate a restricted one
   ([edid.md](edid.md)).
2. Copy `profiles/sony-trinitron-lontium.conf` to a new name with a matching
   `PROFILE_ID`, then update the identity fields, hashes and calibrated margins (and the
   timings only if needed).
3. `sudo ./install.sh --profile NEW --dry-run`, then install and verify on the hardware.
