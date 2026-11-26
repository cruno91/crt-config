# reference/

Files from the original hand-made setup that this project replaces. Nothing here is
installed. Tests use two of them: `crt-xrandr.c` (the new helper must send the same mode
request) and `patch-edid-crt.py` (the generator must produce the same bytes).

All files are verbatim except `cmdline.txt` (`root=PARTUUID` redacted) and
`lightdm-effective-lines.conf` (non-comment lines only, user name redacted).
`ARCHIVE-SHA256SUMS` lists the hashes of the originals; `tests/unit/test_static.py`
checks the verbatim copies against it.

These are one machine's state, not templates. Don't copy them onto a Pi.
