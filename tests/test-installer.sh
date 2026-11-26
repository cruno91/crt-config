#!/bin/sh
# Offline test suite. Installer runs use throw-away staged roots with mocked
# system commands; nothing on this machine is modified. C-helper tests skip
# without X11/XRandR headers (tests/run-container.sh has them).
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd "$here/.."
python3 -B -W error::ResourceWarning -m unittest discover -s tests/unit -p 'test_*.py' "$@"
