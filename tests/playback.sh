#!/bin/sh
# Video-only CRT playback test (optional diagnostic; needs mpv and ffmpeg).
#
# Generates a synthetic 4:3 test clip and plays it fullscreen with
# --monitoraspect=4:3. Audio is never opened: HDMI audio corrupts the picture
# (docs/known-issues.md).
#
# Run as the desktop user, not root, e.g. over SSH:
#   DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" tests/playback.sh --seconds 120
set -eu

seconds=0
file=
while [ $# -gt 0 ]; do
    case $1 in
        --seconds) seconds=$2; shift ;;
        --file) file=$2; shift ;;
        -h|--help)
            echo "usage: tests/playback.sh [--seconds N] [--file VIDEO]"
            echo "Plays a 4:3 test clip fullscreen with audio disabled. Ctrl+C or q stops it."
            exit 0 ;;
        *) echo "playback.sh: unknown argument $1" >&2; exit 2 ;;
    esac
    shift
done
case $seconds in ''|*[!0-9]*) echo "playback.sh: --seconds must be a number" >&2; exit 2 ;; esac

if [ "$(id -u)" -eq 0 ]; then
    echo "playback.sh: run as the desktop user, not root" >&2
    exit 2
fi
command -v mpv >/dev/null 2>&1 || { echo "playback.sh: mpv is not installed (sudo apt install mpv)" >&2; exit 2; }

if [ -z "$file" ]; then
    cache=${XDG_CACHE_HOME:-$HOME/.cache}/rpi-crt
    file=$cache/crt-test.mp4
    if [ ! -s "$file" ]; then
        command -v ffmpeg >/dev/null 2>&1 || { echo "playback.sh: ffmpeg is needed to generate the test clip" >&2; exit 2; }
        mkdir -p "$cache"
        echo "Generating $file ..."
        ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'testsrc2=size=640x480:rate=30000/1001' \
            -t 60 -c:v libx264 -preset fast -pix_fmt yuv420p -an "$file"
    fi
fi

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
set -- mpv --fullscreen --monitoraspect=4:3 --no-audio --loop-file=inf "$file"
echo "Playing with audio disabled: $*"
if [ "$seconds" -gt 0 ]; then
    timeout "$seconds" "$@" || [ $? -eq 124 ]
else
    "$@"
fi
