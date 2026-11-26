# Test fixtures

Derived from `reference/` unless noted. Machine identifiers are fake.

| Fixture | Source |
|---|---|
| `cmdline.txt.stock` | archived line without the CRT arguments, fake PARTUUID |
| `cmdline.txt.legacy` | archived line, fake PARTUUID |
| `config.txt.stock` / `.legacy` | archived `config.txt` without / with `,noaudio` |
| `lightdm.conf.stock` | archived file with the stock `display-setup-script=/usr/share/dispsetup.sh` re-enabled and the hand-made hook removed; user `pi` |
| `lightdm.conf.legacy` | archived file, user `pi` |
| `initramfs-hook-crt-edid.legacy`, `crt-display-setup.sh.legacy` | archived hand-made files |
| `xrandr-verbose.archived.txt` | "Display Configuration" section of the archived inventory |
| `drm-state.active.txt` | **synthetic** debugfs state modelled on the archived "Active DRM Timings" (6 CRTCs, one active) |
| `overlays-README.excerpt` | **reconstructed** excerpt of `/boot/firmware/overlays/README`; the real file was not archived |
