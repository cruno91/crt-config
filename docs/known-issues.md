# Known issues

## HDMI audio corrupts the picture (unresolved)

Playing audio over HDMI (`speaker-test` on ALSA, YouTube, MPV) caused severe video
corruption: horizontal lines, pink/green colour shifts, flicker. It produced no usable
sound. No mode gave reliable audio and video together:

- standard 59.94/60 Hz 480i, and the custom 720×480i;
- 1440×480i without double clock;
- 2880×480i, which produced white noise.

The root cause is unknown: the VC4 driver, the HDMI transmitter, the converter, or how
they interact.

The default policy `disabled` adds `noaudio` to the `vc4-kms-v3d` overlay. No HDMI ALSA
device then exists. This is a mitigation, not a verified fix. Playback with MPV's
`--no-audio` was stable after a full power cycle, but no long test has shown that
`noaudio` prevents the corruption. `verify.sh` checks the policy from `config.txt` and
`/proc/asound/cards` and never plays audio. If corruption appears, do a full power cycle
([recovery.md](recovery.md#hdmi-signal-corruption)).

For sound, the planned (untested) route is a USB stereo DAC into the TV's red/white
inputs. The Pi 5 has no analogue audio output.

## Non-standard back porch

The custom mode moves horizontal sync 40 pixels later than standard 480i (H sync
779-841 instead of 739-801). This centred the picture and removed upper-left distortion
on this TV, but it leaves a 17-pixel back porch where the standard is 57. Other displays
may not accept it. Don't shorten it further without diagnostics (`src/crt-shift.c`).

## Non-square pixels

The 720×480 framebuffer fills a 4:3 picture, so its pixels are about 8:9, and the
margins scale it further. Players must be told the physical aspect, for example
`mpv --monitoraspect=4:3`. With the calibrated margins, `tests/geometry.py` draws a
273×240 circle that should look round.

## Geometry is per television

The margins were set by eye on one TV. Another set of the same model can differ because
of tolerances, age and service-menu settings, so calibrate and make a profile per TV.

## Fallback is standard 480i

If the custom mode can't be applied, X stays on the kernel's standard 480i mode from the
restricted EDID: centred differently and without margins, but never progressive.

## DoubleClock is invisible to `xrandr(1)`

`xrandr --verbose` doesn't show pixel repetition (`RR_DoubleClock`), and `xrandr
--newmode` can't set it, which is why `crt-xrandr` exists. To check it, use
`crt-xrandr --query` (X side) or the `0x101a` flags in the DRM debugfs state (kernel
side); `verify.sh` does both.
