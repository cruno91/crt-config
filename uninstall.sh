#!/bin/sh
# rpi-crt-profiles: see README.md. All logic lives in lib/rpicrt (Python stdlib).
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    echo "uninstall.sh: python3 is required" >&2
    exit 1
fi
# -I: isolated mode (ignores PYTHON* variables and user site-packages); -B: no __pycache__ in the checkout.
exec python3 -I -B "$here/lib/rpicrt/cli.py" uninstall "$@"
