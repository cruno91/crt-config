#!/bin/sh
# Full offline suite in Debian trixie (the Raspberry Pi OS base) with real
# X11/XRandR/libdrm headers, Xvfb and shellcheck, as root and as a user.
# The checkout is mounted read-only. This tests no VC4/HDMI behaviour.
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
image=rpi-crt-test:trixie
if ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" "$here/container"
fi
docker run --rm -v "$here/..:/src:ro" "$image" sh -ec '
    cp -a /src /work && cd /work
    echo "== edid-decode --check (intentional conformance failures expected)"
    edid-decode --check edid/converter-crt.bin >/dev/null 2>&1 || echo "edid-decode exit $?"
    echo "== suite as root (includes initramfs hook tests)"
    RPI_CRT_CONTAINER=1 tests/test-installer.sh "$@"
    echo "== suite as an unprivileged user"
    chown -R tester /work
    su tester -c "cd /work && tests/test-installer.sh"
' sh "$@"
