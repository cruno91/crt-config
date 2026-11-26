#!/bin/sh
# Managed by rpi-crt-profiles - profile @@PROFILE_ID@@. Do not edit.
#
# Copies the EDID firmware referenced by drm.edid_firmware= on the kernel
# command line into the initramfs, so the override is available whenever the
# VC4 driver probes HDMI. It fails loudly if a referenced file is missing:
# booting without it would expose the converter's original EDID (720p
# preferred), which gives an unusable picture on the CRT.

PREREQ=""

case "$1" in
    prereqs)
        echo "$PREREQ"
        exit 0
        ;;
esac

set -e

# shellcheck disable=SC2043  # one file today; the list is rendered from the profile
for file in @@EDID_FILES@@; do
    src="/lib/firmware/edid/$file"
    if [ ! -f "$src" ]; then
        echo "rpi-crt-edid: $src is missing; reinstall the CRT profile or remove drm.edid_firmware= from cmdline.txt" >&2
        exit 1
    fi
    mkdir -p "${DESTDIR}/lib/firmware/edid"
    cp "$src" "${DESTDIR}/lib/firmware/edid/$file"
done
