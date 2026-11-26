# Recovery

The installer never touches SSH or networking, so SSH stays available whatever happens
to the picture. Commands run over SSH from the repository checkout.

## Bad picture after installing

```sh
sudo ./verify.sh
tail -n 50 /var/log/rpi-crt/display-setup.log      # or: journalctl -t rpi-crt -b
sudo ./uninstall.sh && sudo reboot                 # revert everything
```

If the display setup fails (converter unplugged, X unreachable, mode rejected), it logs
the error and LightDM starts on the kernel's standard 720×480i mode: off-centre and
without margins, but never progressive. To retry it by hand:

```sh
sudo env DISPLAY=:0 XAUTHORITY=/var/run/lightdm/root/:0 /usr/local/libexec/rpi-crt/display-setup
```

`crt-xrandr` exit code 3 means a different mode with the same name exists. Switch the
output to another mode, then run `crt-xrandr --replace`. Exit code 4 means the output was
not found.

## The Pi doesn't boot or SSH is unavailable

On another computer, copy these files over the originals on the SD card's boot
partition:

```text
cmdline.txt.rpi-crt-baseline   -> cmdline.txt
config.txt.rpi-crt-baseline    -> config.txt
```

This removes the EDID override, the `video=` argument and the `noaudio` change. Then run
`sudo ./uninstall.sh` on the Pi to remove the remaining files. Alternatively, delete only
`drm.edid_firmware=… video=HDMI-A-1:…` from `cmdline.txt`, keeping it on one line.

## LightDM doesn't start

```sh
sudo journalctl -u lightdm -b
sudo cp /var/lib/rpi-crt/baseline/etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf
```

The `cp` restores the original LightDM configuration and discards any later edits. The
generated script only exits non-zero when a chained pre-existing hook fails.

## HDMI signal corruption

Pink/green bands, horizontal lines and flicker come from the HDMI audio problem
([known-issues.md](known-issues.md)). An ordinary reboot does not clear it:

1. Stop playback, then `sudo poweroff`.
2. Disconnect power from the Pi **and** the converter, and unplug HDMI so the converter
   isn't powered through it.
3. Wait about 30 seconds.
4. Reconnect HDMI, power the converter, then the Pi.

Don't play HDMI audio to test whether it's fixed.

## Interrupted install

Each step is journaled in `/var/lib/rpi-crt/manifest.json` before it runs, and
`cmdline.txt` is edited last. Rerun `install.sh` to resume, or `uninstall.sh` to roll
back what was done.

## Uninstall reports conflicts

Uninstall leaves anything changed since installing alone and lists it:

- **An edited boot argument**: decide what `cmdline.txt` should contain. The original is
  in `/var/lib/rpi-crt/baseline/boot/firmware/cmdline.txt`.
- **"kept … because cmdline.txt still references it"**: the EDID stays while the kernel
  would load it; removing it would bring back the converter's 720p EDID.
- **"… was modified after installation"**: restore or delete the file yourself.

Then rerun `sudo ./uninstall.sh`. `install.sh` refuses to run until uninstall finishes.
Originals stay in `/var/lib/rpi-crt/baseline/`, and a finished uninstall moves its
state to `/var/lib/rpi-crt/history/`.

## Taking over a hand-made setup

```sh
sudo ./verify.sh --profile sony-trinitron-lontium --runtime-only   # read-only check
sudo ./install.sh --profile sony-trinitron-lontium --migrate-legacy --dry-run
sudo ./install.sh --profile sony-trinitron-lontium --migrate-legacy
```

This points LightDM at the managed script and moves
`/etc/initramfs-tools/hooks/crt-edid` into `/var/lib/rpi-crt/legacy/`. That hook also
copies a `converter-480i.bin` which may not exist. `/usr/local/bin/crt-xrandr` and
`crt-display-setup.sh` stay in place, unused. Uninstall restores the hand-made state
exactly.

## Recalibrating geometry

Margins can be changed live (0-100; they reset at the next LightDM start):

```sh
sudo env DISPLAY=:0 XAUTHORITY=/var/run/lightdm/root/:0 \
  xrandr --output HDMI-1 --set "left margin" 8 --set "right margin" 48 \
         --set "top margin" 16 --set "bottom margin" 16
```

Put the result in a new profile, uninstall the old one, and install the new one. To move
the horizontal sync position, build `src/crt-shift.c` and run it with LightDM stopped.
