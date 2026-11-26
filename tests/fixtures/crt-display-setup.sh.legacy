#!/bin/bash
set -e

if ! xrandr --query | grep -q '720x480i-crt'; then
    /usr/local/bin/crt-xrandr
fi

xrandr --output HDMI-1 \
    --mode 720x480i-crt \
    --set "left margin" 8 \
    --set "right margin" 48 \
    --set "top margin" 16 \
    --set "bottom margin" 16
