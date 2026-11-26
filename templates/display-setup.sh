#!/bin/sh
# Managed by rpi-crt-profiles - profile @@PROFILE_ID@@. Do not edit: changes are
# overwritten by install.sh and detected as conflicts by uninstall.sh.
#
# LightDM runs this as root (display-setup-script) after the X server starts,
# with DISPLAY and XAUTHORITY already set. It registers the profile's custom
# mode (with HDMI pixel repetition, which xrandr(1) cannot express) through
# crt-xrandr, then selects it and applies the calibrated margins.
#
# Contract: it never blocks LightDM for long (every X call has a timeout) and
# never fails the seat because of the CRT profile. On any error it logs the
# problem and leaves the X server on the kernel's restricted standard 480i
# mode from the EDID, which is the documented fallback.
#
# Manual run over SSH (XAUTHORITY path may differ):
#   sudo env DISPLAY=:0 XAUTHORITY=/var/run/lightdm/root/:0 @@DISPLAY_SETUP@@
# Logs: @@LOG_FILE@@ and the journal (journalctl -t rpi-crt).

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 022

PROFILE='@@PROFILE_ID@@'
OUTPUT='@@XRANDR_OUTPUT@@'
MODE='@@MODE_NAME@@'
HELPER='@@HELPER@@'
LOG_FILE='@@LOG_FILE@@'
CHAIN='@@CHAINED_HOOK@@'
X_TIMEOUT=10

log() {
    msg="$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null) [$PROFILE] $*"
    log_dir=${LOG_FILE%/*}
    if [ -d "$log_dir" ] || mkdir -p "$log_dir" 2>/dev/null; then
        if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)" -gt 262144 ]; then
            tail -n 400 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv -f "$LOG_FILE.tmp" "$LOG_FILE"
        fi
        printf '%s\n' "$msg" >> "$LOG_FILE" 2>/dev/null || :
    fi
    if command -v logger >/dev/null 2>&1; then
        logger -t rpi-crt -- "$msg" 2>/dev/null || :
    fi
    printf '%s\n' "$msg" >&2
}

run_x() {
    if command -v timeout >/dev/null 2>&1; then
        timeout "$X_TIMEOUT" "$@"
    else
        "$@"
    fi
}

apply_profile() {
    if ! query=$(run_x xrandr --query 2>&1); then
        log "ERROR: xrandr --query failed (DISPLAY=${DISPLAY:-unset}): $query"
        return 1
    fi
    if ! printf '%s\n' "$query" | grep -q "^$OUTPUT connected"; then
        log "ERROR: output $OUTPUT is not connected (converter absent or powered off?)"
        return 1
    fi

    out=$(run_x "$HELPER" @@HELPER_ARGS@@ 2>&1)
    rc=$?
    case $rc in
        0) ;;
        3) log "ERROR: a different mode named $MODE already exists; run crt-xrandr --replace manually: $out"
           return 1 ;;
        4) log "ERROR: crt-xrandr cannot find output $OUTPUT: $out"
           return 1 ;;
        *) log "ERROR: crt-xrandr failed with exit code $rc: $out"
           return 1 ;;
    esac

    if ! out=$(run_x xrandr --output "$OUTPUT" --mode "$MODE" \
            --set "left margin" @@MARGIN_LEFT@@ \
            --set "right margin" @@MARGIN_RIGHT@@ \
            --set "top margin" @@MARGIN_TOP@@ \
            --set "bottom margin" @@MARGIN_BOTTOM@@ 2>&1); then
        log "ERROR: could not select $MODE with margins on $OUTPUT: $out"
        return 1
    fi

    if ! out=$(run_x "$HELPER" @@HELPER_ARGS@@ --query --require-active 2>&1); then
        log "ERROR: $MODE is not active on $OUTPUT after selection: $out"
        return 1
    fi
    log "applied $MODE on $OUTPUT with margins L@@MARGIN_LEFT@@ R@@MARGIN_RIGHT@@ T@@MARGIN_TOP@@ B@@MARGIN_BOTTOM@@"
    return 0
}

# A display-setup-script that was configured before installation (for example
# Raspberry Pi OS's /usr/share/dispsetup.sh) runs first, so that a stored
# screen layout cannot override the CRT mode afterwards. Its exit status is
# preserved, exactly as LightDM would have seen it without this profile.
chain_rc=0
if [ -n "$CHAIN" ]; then
    if [ -x "$CHAIN" ]; then
        log "running previously configured display-setup-script $CHAIN"
        "$CHAIN"
        chain_rc=$?
        [ "$chain_rc" -eq 0 ] || log "WARNING: $CHAIN exited with status $chain_rc"
    else
        log "WARNING: previously configured display-setup-script $CHAIN is missing or not executable; skipped"
    fi
fi

if ! apply_profile; then
    log "FALLBACK: leaving $OUTPUT on the kernel's standard 480i mode; see docs/recovery.md"
fi
exit "$chain_rc"
